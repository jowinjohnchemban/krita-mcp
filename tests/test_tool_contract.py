"""Every tool, by name: it is registered, described, has a valid input schema and declares all four MCP hints."""
import asyncio

import pytest

from kritapilot import server

ALL_TOOLS = (
    "add_svg", "add_vignette", "adjust_image", "ai_backends", "analyze_image", "animation_contact_sheet",
    "animation_info", "animation_set", "apply_filter", "arrange_layer", "ask_vision", "auto_enhance", "batch",
    "brush_stroke", "camera_move", "canvas_snapshot", "colorize_lineart", "convert_color_space", "create_artwork",
    "create_layer", "crop_image", "delete_layer", "describe_filter", "dismiss_recovery_dialog", "document_activate",
    "document_close", "document_info", "document_new", "document_open", "draw_shapes", "duplicate_layer",
    "edit_image", "export_animation_frames", "export_image", "export_layers", "export_selection_mask",
    "fill_selection", "find_color", "find_skills", "flatten_image", "flip", "for_each_frame", "generate_image",
    "generate_video", "generation_history", "history", "import_asset", "import_frames", "import_image",
    "import_image_from_url", "import_video", "ink_stroke", "keyframe", "krita_action", "krita_status",
    "layer_colors", "list_brush_presets", "list_filters", "list_keyframes", "list_layers", "list_resources",
    "list_vector_shapes", "list_workspace", "match_colors", "merge_down", "modify_selection", "notify_user",
    "outline_layer", "outpaint", "palette_colors", "pick_variation", "place_generation", "quantize_layer",
    "read_pixels", "redo", "remove_background", "remove_vector_shapes", "render_video", "resize_canvas",
    "rotate_image", "sample_color", "save_document", "scale_image", "search_assets", "select_by_description",
    "set_guides", "set_selection", "slice_sheet", "swap_colors", "transform_layer", "undo", "update_layer",
    "upscale_image", "use_skill", "user_tool_state", "video_info", "view_control", "view_image",
)


@pytest.fixture(scope="module")
def listed():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_the_list_above_is_every_tool(listed):
    assert set(ALL_TOOLS) == set(listed) == set(server.TOOLS) and len(ALL_TOOLS) == len(set(ALL_TOOLS))


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_tool_is_described_and_declares_all_four_hints(listed, name):
    tool = listed[name]
    assert len((tool.description or "").strip()) > 20, "needs a description that says what it does"
    schema = getattr(tool, "inputSchema", None) or tool.input_schema  # SDK 1.x / 2.x
    assert schema["type"] == "object" and set(schema.get("required", [])) <= set(schema["properties"])
    ann = tool.annotations
    assert ann is not None and ann.title
    for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
        value = getattr(ann, hint, None)
        if value is None:  # SDK 2.x names
            value = getattr(ann, "".join("_" + c.lower() if c.isupper() else c for c in hint), None)
        assert isinstance(value, bool), "%s: %s must be true or false" % (name, hint)
    read_only = getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None))
    idempotent = getattr(ann, "idempotentHint", getattr(ann, "idempotent_hint", None))
    assert idempotent or not read_only, "a read-only tool is always idempotent"
