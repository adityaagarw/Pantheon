//! The engine: Pantheon's Python runtime, run as a child process on 127.0.0.1.
//!
//! In development it runs from the repo's `engine/` folder with `uv run`. An installed app
//! runs its bundled runtime (scripts/bundle_engine.py): a standalone Python with the
//! engine's dependencies, in the app's resources as `runtime/`. The engine writes its log to
//! `<data>/logs/engine.log` and prints `PANTHEON_READY <port>` once it listens.
//!
//! It never outlives the app, even after a crash: on Windows it runs in a job object
//! that kills everything in it when the app's handle closes; on macOS and Linux it
//! exits when its parent is gone (PANTHEON_EXIT_WITH_PARENT).

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

pub const DEFAULT_PORT: u16 = 8720;

pub struct Engine {
    child: Mutex<Option<Child>>,
    #[cfg(windows)]
    _job: job::Job,
    pub port: u16,
    pub log_path: PathBuf,
    ready: Arc<(Mutex<Option<bool>>, Condvar)>,
}

/// 8720 if it's free (or PANTHEON_PORT), otherwise any free port.
pub fn choose_port() -> u16 {
    let wanted = std::env::var("PANTHEON_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(DEFAULT_PORT);
    if TcpListener::bind(("127.0.0.1", wanted)).is_ok() {
        return wanted;
    }
    TcpListener::bind(("127.0.0.1", 0)).and_then(|l| l.local_addr()).map(|a| a.port()).unwrap_or(wanted)
}

/// The repo's engine folder, for development runs.
fn dev_engine_dir() -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("PANTHEON_ENGINE_DIR") {
        return Some(PathBuf::from(dir));
    }
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
    dir.join("pyproject.toml").exists().then(|| dir.canonicalize().unwrap_or(dir))
}

/// The bundled runtime's Python, if this is an installed app.
fn bundled_python(runtime: &Path) -> Option<PathBuf> {
    let py = if cfg!(windows) { runtime.join("python").join("python.exe") } else { runtime.join("python/bin/python3") };
    py.exists().then_some(py)
}

fn command(data_dir: &Path, port: u16, runtime: Option<&Path>) -> Result<Command, String> {
    let mut cmd = match runtime.and_then(|r| bundled_python(r).map(|py| (r, py))) {
        Some((runtime, py)) => {
            let engine = runtime.join("engine");
            let mut cmd = Command::new(py);
            cmd.args(["-m", "app.serve"])
                .current_dir(&engine)
                .env("PYTHONPATH", &engine)
                // An AppImage's launcher points PYTHONHOME at the image: the bundled Python
                // must use its own.
                .env_remove("PYTHONHOME")
                .env("PYTHONNOUSERSITE", "1")
                .env("PYTHONDONTWRITEBYTECODE", "1");
            cmd
        }
        None => {
            let dir = dev_engine_dir().ok_or("the engine folder wasn't found (set PANTHEON_ENGINE_DIR)")?;
            let mut cmd = Command::new("uv");
            cmd.args(["run", "--project"]).arg(&dir).args(["python", "-m", "app.serve"]).current_dir(&dir);
            cmd
        }
    };
    cmd.env("PANTHEON_DATA_DIR", data_dir)
        .env("PANTHEON_PORT", port.to_string())
        .env("PANTHEON_EXIT_WITH_PARENT", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    Ok(cmd)
}

impl Engine {
    pub fn start(data_dir: &Path, runtime: Option<&Path>) -> Result<Engine, String> {
        let port = choose_port();
        let logs = data_dir.join("logs");
        fs::create_dir_all(&logs).map_err(|e| format!("can't create {}: {e}", logs.display()))?;
        let log_path = logs.join("engine.log");
        #[cfg(windows)]
        let job = job::Job::new()?;
        let mut child = command(data_dir, port, runtime)?
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("couldn't start the engine: {e}"))?;
        #[cfg(windows)]
        job.assign(&child)?;

        let ready = Arc::new((Mutex::new(None), Condvar::new()));
        let log = Arc::new(Mutex::new(
            OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?,
        ));
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        pipe(stdout, log.clone(), Some(ready.clone()));
        pipe(stderr, log, None);

        Ok(Engine {
            child: Mutex::new(Some(child)),
            #[cfg(windows)]
            _job: job,
            port,
            log_path,
            ready,
        })
    }

    /// Wait until the engine listens. False if it exited or didn't start in time.
    pub fn wait_ready(&self, timeout: Duration) -> bool {
        let (lock, cv) = &*self.ready;
        let state = lock.lock().unwrap();
        let (state, _) = cv.wait_timeout_while(state, timeout, |s| s.is_none()).unwrap();
        state.unwrap_or(false)
    }

    /// Stop the engine and everything it started (the job object takes the whole tree
    /// on Windows; elsewhere the engine notices its parent is gone).
    pub fn stop(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            #[cfg(windows)]
            self._job.terminate();
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Copy a stream of the engine's output into its log; on stdout, watch for READY.
fn pipe<R: std::io::Read + Send + 'static>(
    stream: R,
    log: Arc<Mutex<File>>,
    ready: Option<Arc<(Mutex<Option<bool>>, Condvar)>>,
) {
    thread::spawn(move || {
        let mut found = false;
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            if !found && line.starts_with("PANTHEON_READY") {
                found = true;
                if let Some(r) = &ready {
                    *r.0.lock().unwrap() = Some(true);
                    r.1.notify_all();
                }
            }
            let _ = writeln!(log.lock().unwrap(), "{line}");
        }
        // The stream closed: the engine exited.
        if let Some(r) = ready {
            let mut s = r.0.lock().unwrap();
            if s.is_none() {
                *s = Some(false);
                r.1.notify_all();
            }
        }
    });
}

/// A Windows job object that kills every process in it when its last handle closes:
/// when the app exits, or crashes, the OS ends the engine and everything it started.
#[cfg(windows)]
mod job {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(HANDLE);

    // The handle is only used through the Win32 calls below, which are thread-safe.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> Result<Job, String> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err("couldn't create a job object for the engine".into());
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const _,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    CloseHandle(handle);
                    return Err("couldn't configure the engine's job object".into());
                }
                Ok(Job(handle))
            }
        }

        /// Put the process in the job; processes it starts later join it too.
        pub fn assign(&self, child: &Child) -> Result<(), String> {
            let ok = unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                return Err("couldn't put the engine in its job object".into());
            }
            Ok(())
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 0);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
