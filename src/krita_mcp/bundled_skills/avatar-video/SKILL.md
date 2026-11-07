---
name: avatar-video
description: Make a talking avatar or presenter video from a script - spokesperson, explainer, greeting - with HeyGen, choosing an avatar and voice.
---
# Talking avatar video

1. Needs HeyGen signed in (AI Chat > Services). `ai_backends(details=true)` lists the account's avatars and
   voices.
2. Write the script short and spoken: 30-60 seconds, short sentences, no lists or symbols.
3. `generate_video(backend="heygen", script="...", avatar_id=..., voice_id=..., character="avatar")`.
   Without ids the account's first avatar and voice are used - ask the user which they want.
4. Video credits are expensive: confirm the script with the user before generating, and make one take.
5. The clip is imported into Krita; add titles or a lower-third with `add_svg` and render with `render_video`.
