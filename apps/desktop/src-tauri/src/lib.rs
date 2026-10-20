//! Pantheon Studio's shell: it starts the engine, opens the window and stops the engine
//! when the app quits.

mod engine;

use std::time::Duration;

use engine::Engine;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

#[tauri::command]
fn engine_info(state: tauri::State<'_, Engine>) -> serde_json::Value {
    serde_json::json!({ "port": state.port, "log": state.log_path })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, None))
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(|app| {
            // PANTHEON_DATA_DIR keeps the data elsewhere (portable use, installer checks).
            let data_dir = match std::env::var_os("PANTHEON_DATA_DIR") {
                Some(d) => std::path::PathBuf::from(d),
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&data_dir)?;
            // An installed app carries its engine in its resources; a development build has
            // none there and runs the repo's engine with uv.
            let runtime = app.path().resource_dir().ok().map(|d| d.join("runtime")).filter(|d| d.is_dir());
            let engine = Engine::start(&data_dir, runtime.as_deref())
                .map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            let port = engine.port;
            app.manage(engine);

            // The UI reads the port from here (ui/src/lib/engine.ts) and waits for the
            // engine itself, so the window opens at once.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Pantheon Studio")
                .inner_size(1440.0, 900.0)
                .min_inner_size(960.0, 600.0)
                .initialization_script(&format!("window.__PANTHEON__ = {{ enginePort: {port} }};"))
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let engine = handle.state::<Engine>();
                if !engine.wait_ready(Duration::from_secs(120)) {
                    log::error!("the engine didn't start; see {}", engine.log_path.display());
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Pantheon Studio")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                app.state::<Engine>().stop();
            }
        });
}
