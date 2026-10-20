; Pantheon Studio installer hooks (tauri.bundle.json: bundle.windows.nsis.installerHooks).
; pantheon-serve on PATH: a one-line launcher in WindowsApps, which Windows puts on every
; user's PATH, so nothing needs editing.

!macro NSIS_HOOK_POSTINSTALL
  CreateDirectory "$LOCALAPPDATA\Microsoft\WindowsApps"
  FileOpen $0 "$LOCALAPPDATA\Microsoft\WindowsApps\pantheon-serve.cmd" w
  FileWrite $0 '@"$INSTDIR\runtime\pantheon-serve.cmd" %*$\r$\n'
  FileClose $0
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$LOCALAPPDATA\Microsoft\WindowsApps\pantheon-serve.cmd"
!macroend
