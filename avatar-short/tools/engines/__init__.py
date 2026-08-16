"""⑥slides のエンジン。

同じ契約を満たす実装を2つ持つ（§7-1）:
  render(run_dir, style_path, theme_path, *, quality, keep_intermediate) -> Path
  available() -> bool
  setup_hint() -> str

出口は必ず runs/<slug>/slides.mp4（不透過・1080x1920・30fps）。
"""
