"""ComfyUI Bing Image Selector custom node and preview routes."""
import asyncio
import json
import re
from pathlib import Path

import folder_paths
import numpy as np
import torch
from aiohttp import web
from PIL import Image, ImageOps
from server import PromptServer

from .bing import prepare_search, resolve_selection, session_path, output_dimensions

WEB_DIRECTORY = "./web"


def cache_root():
    return Path(folder_paths.get_input_directory()) / "bing_image_selector"


class BingImageSelector:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "search_term": ("STRING", {"default": "landscape", "multiline": False}),
            "number_of_images": ("INT", {"default": 12, "min": 1, "max": 64, "step": 1}),
            "width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
            "height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
            "sizing_mode": (["width_height", "megapixels"],),
            "megapixels": ("FLOAT", {"default": 1.0, "min": 0.05, "max": 16.0, "step": 0.05}),
            "resolution_interval": (["32", "8", "16", "64", "128"],),
            "resize_mode": (["pad", "crop", "stretch"],),
            "selection": ("STRING", {"default": "{}", "multiline": False}),
        }}

    RETURN_TYPES = ("IMAGE", "INT", "STRING")
    RETURN_NAMES = ("images", "selected_count", "source_urls")
    FUNCTION = "load_selected"
    CATEGORY = "image/Bing"
    DESCRIPTION = "Search and select thumbnails before queuing. Outputs the selected images as one IMAGE batch."

    @classmethod
    def VALIDATE_INPUTS(cls, selection, search_term, number_of_images):
        try:
            resolve_selection(cache_root(), selection, search_term, number_of_images)
            return True
        except ValueError as error:
            return str(error)

    def load_selected(self, search_term, number_of_images, width, height, resize_mode, selection, sizing_mode="width_height", megapixels=1.0, resolution_interval="32"):
        items, paths = resolve_selection(cache_root(), selection, search_term, number_of_images)
        width, height = output_dimensions(width, height, sizing_mode, megapixels, resolution_interval)
        if len(paths) * width * height > 64 * 1024 * 1024:
            raise ValueError("This batch is too large. Reduce the selected count or output dimensions (limit: 64 million pixels).")
        if resize_mode not in ("pad", "crop", "stretch"):
            raise ValueError("Unknown resize mode.")
        batch = torch.empty((len(paths), height, width, 3), dtype=torch.float32)
        for index, path in enumerate(paths):
            with Image.open(path) as original:
                image = original.convert("RGB")
                if resize_mode == "pad":
                    image = ImageOps.pad(image, (width, height), method=Image.Resampling.LANCZOS, color="black")
                elif resize_mode == "crop":
                    image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
                else:
                    image = image.resize((width, height), Image.Resampling.LANCZOS)
                batch[index] = torch.from_numpy(np.asarray(image).astype(np.float32) / 255.0)
        return batch, len(paths), json.dumps([item["url"] for item in items])


_search_lock = asyncio.Lock()


@PromptServer.instance.routes.post("/bing_image_selector/search")
async def search(request):
    if _search_lock.locked():
        return web.json_response({"error": "Another image search is running. Please try again shortly."}, status=429)
    async with _search_lock:
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid search request.")
            result = await asyncio.to_thread(prepare_search, cache_root(), data.get("query"), data.get("count"))
            return web.json_response(result)
        except (ValueError, TypeError) as error:
            return web.json_response({"error": str(error)}, status=400)
        except Exception:
            return web.json_response({"error": "Bing search failed. Check your internet connection and try again."}, status=502)


@PromptServer.instance.routes.get("/bing_image_selector/thumbnail/{session}/{image_id}")
async def thumbnail(request):
    try:
        directory = session_path(cache_root(), request.match_info["session"])
        key = request.match_info["image_id"]
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid image ID")
        path = directory / f"{key}.jpg"
        if not path.is_file():
            raise ValueError("Missing thumbnail")
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})
    except ValueError:
        raise web.HTTPNotFound() from None


NODE_CLASS_MAPPINGS = {"BingImageSelector": BingImageSelector}
NODE_DISPLAY_NAME_MAPPINGS = {"BingImageSelector": "Bing Image Selector"}
