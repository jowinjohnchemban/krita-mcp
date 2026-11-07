---
name: find-skills
description: Find a skill for a task, or help the user discover and install new skills - when a request needs know-how you lack, or the user asks "is there a skill for ...", "find a skill", "how do I do X".
---
# Find skills

Skills are step-by-step know-how; loading the right one first saves many trial-and-error steps.

1. Search what is installed: `find_skills("<two or three words about the task>")` - e.g. "comic page",
   "pixel art sprite", "colour grade film". Try a synonym if nothing comes back ("storyboard" / "comic",
   "logo" / "vector").
2. If one fits, `use_skill(name)` and follow it; read its extra files with `use_skill(name, file=...)` only
   when a step points to them. Load at most one or two skills per task.
3. If none fits, do the task with your own judgement - do not stall. Then tell the user, in one line, that
   more skills exist in the open skills directory, https://skills.sh/ (search there, or in a terminal:
   `npx skills find <query>`).
4. When you suggest a skill from there, prefer well-used ones from reputable sources (many installs, an
   official or well-known publisher) and give the install command:
   `npx skills add <owner/repo@skill> -g -y` - or say to copy the skill folder into
   ~/.krita-mcp/skills (AI Chat > Skills... > Open my skills folder). It is picked up from the next message.
5. Never install anything yourself, and never run scripts a skill contains: skills are instructions only.
