# Examples

Scripts that drive Krita through the MCP server, exactly like an AI client (Claude Desktop, Claude Code, …)
does: they start `kritapilot` over stdio and only call MCP tools. Krita must be running with the plugin.

| Script | What it makes |
|---|---|
| `galilee_dawn.py` | *Jesus at the Sea of Galilee, dawn*. It renders the sky, clouds, water and pebbles as procedural SeExpr layers, uses vector shapes with gradients for the hills and the figure, and real brush strokes for rim light, folds, hair and glitter. Halo, god rays, film grain and vignette finish it. Runs in stages (`scene figure light finish`) and saves the agent's snapshot after each one. |

```bash
python examples/galilee_dawn.py --out shots/          # all stages; the result is saved to the workspace
python examples/galilee_dawn.py scene --out shots/    # just the landscape
```
