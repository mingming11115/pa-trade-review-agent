# Upstream drawing demo UI

Source: https://github.com/deepentropy/lightweight-charts-drawing

Pinned commit: `0291d9c7373e3795373c915a3a64f4f663b5a0db`, package version `0.6.2`, MIT (see LICENSE).

The following are byte-for-byte copies from that commit:

- `demo.ts.txt` from `demo/demo.ts` (the complete reference implementation)
- `demo.css`, `icons.ts`, `tool-groups.ts`, `index.html` from `demo/`
- `LICENSE` from the repository root

`mountUpstreamDrawingUI.ts` is the demo controller adapted to an existing PA DrawingManager. It retains all 86 tool entries, SVG icons, grouped flyouts, emoji picker, magnet, keep-drawing, visibility, remove-all, selection bar, inline text/table editor, and image input. The demo's entire top header row, including Export/Import, Tools, npm, and GitHub links, is omitted from the chart toolbar.

Host adaptations only:

1. Import the installed npm package instead of `../src`; remove the SPY CSV fetch, chart creation and demo window/debug globals. PA supplies chart, series, bars and manager.
2. Resolve IDs within a ShadowRoot so multiple chart instances do not collide. Load the upstream CSS and adapted HTML there. Replace the CSS `:root` selector with `:host`; supply the original body font/background at host boundaries. Omit the demo header row because PA already displays chart context and persists drawings locally.
3. Track DOM/manager subscriptions and delayed callbacks for unmount cleanup. Guard pending file reads after unmount.
4. Notify PA when the original editor opens/closes so trading stays locked. Namespace Image asset names by PA scope and persist bytes before adding them; restore through public `setImageReader`.

Do not replace these assets with hand-drawn icons, a smaller tool list, or recreated settings UI. This is the upstream **demo UI**, not a UI component exported by the npm package: that package exports the drawing engine and explicitly leaves host UI to applications.

异步 JSON 导入与图片添加完成前检查宿主是否仍可编辑；加载期间取消本次提交并提示重试，避免提示成功但未保存。
