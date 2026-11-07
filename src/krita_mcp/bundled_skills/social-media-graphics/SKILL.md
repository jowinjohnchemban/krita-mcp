---
name: social-media-graphics
description: Social media and web images at the right size - Instagram posts and stories, YouTube thumbnails, banners, covers, ads - with readable text overlays.
---
# Social media graphics

1. Size first (`document_new`): Instagram post 1080x1350 (4:5) or 1080x1080, story/reel 1080x1920,
   YouTube thumbnail 1280x720, X/LinkedIn banner 1500x500 / 1584x396, Facebook cover 1640x624.
2. Keep important content inside safe margins (`set_guides` about 8% in from each edge; stories: leave the
   top and bottom 250 px free).
3. One clear message: a strong image, a short headline (3-6 words) with `add_svg` <text>, large and bold,
   with contrast (a dark gradient or shape behind light text).
4. Thumbnails: a face or object close up, high contrast and saturation, 2-3 colours, readable at 160 px
   (`canvas_snapshot(max_size=160)`).
5. Export: `export_image("post.jpg", quality=90)` or PNG for graphics with text.
