---
name: video-editing
description: Edit a video in Krita - import a clip, trim, colour-grade every frame, paint or add titles over frames, rotoscope, and render MP4 or GIF.
---
# Video editing

1. `video_info(path)` first, then `import_video(path, start=..., duration=..., fps=..., max_frames=...)` -
   import only the part you need (frames cost memory and time).
2. Grade all frames at once: `for_each_frame([{"tool": "adjust_image", "args": {"operation": "temperature",
   "settings": {"temperature": 20}}}])`.
3. Paint or draw on frames: an animated paint layer, `keyframe(frame, "blank")`, `animation_set(frame=...)`
   and draw; titles on a separate vector layer with `add_svg`.
4. Review with `animation_contact_sheet` instead of many snapshots.
5. Render: `render_video("edit.mp4", fps=..., quality=...)` or `.gif` for short loops.
