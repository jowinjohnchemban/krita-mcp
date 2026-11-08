---
name: still-to-video
description: Turn a picture into motion - camera moves, short animated or AI video clips, talking avatars - and render it to MP4 or GIF.
---
# Still to video

1. Free and instant first: `camera_move(move="dolly_in" | "pan_left" | "orbit_left" | "ken_burns" | ...,
   seconds=3, render_path="shot.mp4")` animates the current picture without any AI model.
2. Real motion (people move, water flows): `generate_video(prompt describing the motion, camera=...,
   use_canvas=true)`. Video models are the most expensive calls: keep clips short (4-6 s), describe one
   clear action, and confirm with the user before making more than one.
3. Bring the clip into Krita with the result's import, check frames with `animation_contact_sheet`, and
   render with `render_video(path)`.
