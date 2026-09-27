# Bing Image Selector for ComfyUI

Search Bing, browse thumbnails **inside the node before running the workflow**, and choose exactly which images to send downstream as a standard `IMAGE` batch.

## Install

1. Copy this folder to `ComfyUI/custom_nodes/bing_image_selector` (the `__init__.py` file must be directly inside that folder).
2. With the Python environment used by ComfyUI, run:
   ```sh
   python -m pip install -r custom_nodes/bing_image_selector/requirements.txt
   ```
   For Windows portable, from the portable installation folder:
   ```powershell
   .\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\bing_image_selector\requirements.txt
   ```
3. Restart ComfyUI and refresh the browser. Add **Bing Image Selector**, under **image / Bing**.

ComfyUI supplies PyTorch and aiohttp. This extension does not need transformers, a model, an API key, or the original ConCarneNode package.

## Use

1. Enter `search_term` and `number_of_images` (1–64). The count is the number of downloadable preview candidates requested, not the number you must select.
2. Click **Search images**. This searches and caches images without executing the workflow. Fewer results may be returned when sources block downloads.
3. Click thumbnails to select them. The numbered badges show **selection/output order**. Click a selected thumbnail to deselect it, or use **Select all** / **Clear**.
4. Choose the sizing controls below, connect `images` to a node accepting `IMAGE` (such as Preview Image or Save Image), and run the workflow.

An empty selection produces an actionable error; the node never silently chooses images for you. Changing the search term or requested count requires a new search. Multiple selector nodes have independent selections, and only one server search runs at a time.

The pre-run search uses values entered directly in this node. An upstream node's generated search term cannot be evaluated before workflow execution; disconnect those inputs for interactive search.

## Output dimensions

- **sizing_mode = width_height:** use the entered width and height, rounded to the chosen interval.
- **sizing_mode = megapixels:** use width:height as the desired aspect ratio, scale to the target pixel count, then round both dimensions to the interval.
- **megapixels:** target area in decimal megapixels (1 MP = 1,000,000 pixels); active only in megapixels mode.
- **resolution_interval:** 8, 16, **32** (default), 64, or 128. Each dimension rounds to the nearest multiple, with ties rounding up. The gallery displays the resulting dimensions and actual megapixels.
- **resize_mode = pad:** fit the entire image, preserving its aspect ratio, with black padding.
- **resize_mode = crop:** fill the output and center-crop the excess, preserving aspect ratio.
- **resize_mode = stretch:** resize directly to the output dimensions, which can distort the image.

For example, a square 1.0 MP target with interval 32 produces **992 × 992** (0.984 MP). Rounding may slightly alter the requested aspect ratio and area. Every selected image gets the same final dimensions so the collection is a valid batch.

Maximum output dimension is 8192. A batch is limited to 64 × 1024 × 1024 pixels to bound memory consumption. Reduce dimensions or selections if this limit is exceeded. Large batches can still consume substantial RAM in downstream nodes.

## Outputs

| Output | Type | Contents |
| --- | --- | --- |
| images | IMAGE | Float32 RGB batch, `[selected_count, height, width, 3]`, values 0–1 |
| selected_count | INT | Number of selected images |
| source_urls | STRING | JSON array of original image URLs in selection order |

## Saved workflows and cache

Downloaded images, thumbnails, and a search manifest are stored under `ComfyUI/input/bing_image_selector/<session>/`. Each search gets its own session. Images are decoded, EXIF-oriented, converted to RGB, and capped at 4096 pixels on the longest side before caching. Animated images use their first frame. Execution reads the cache, so it does not depend on the source image still being available online.

Saved workflows retain preview metadata and selected IDs. To move a workflow to another machine, copy its corresponding cache directory as well, or search again. Cache folders are retained until manually removed; deleting a referenced folder requires re-searching. No automatic cleanup is performed because it could break saved workflows.

Search requires internet access. Bing's HTML and access restrictions can change. The scraper uses moderate SafeSearch. Downloads have timeouts, size limits, and public HTTP(S) address checks. Use this with a trusted local ComfyUI installation. Image usage rights remain with the original sources.

## Implementation and credit

The requested reference is [ConCarneNode's BingImageGrabber](https://github.com/concarne000/ConCarneNode/blob/main/__init__.py). This node independently implements its Bing `/images/async` and `murl` extraction approach, adding pagination, duplicate filtering, bounded downloads, persistent caching, and a pre-execution selector. The unrelated language-model nodes and their dependencies are not included.

The frontend uses ComfyUI's [extension hooks](https://docs.comfy.org/custom-nodes/js/javascript_hooks) and DOM widgets; the backend uses the supported [custom node interface](https://docs.comfy.org/custom-nodes/backend/server_overview).

## Verification

- Backend unit tests: `python -m unittest discover -s tests -v` (requires Pillow and numpy).
- Isolated frontend test: `npm install`, then `npm run test:frontend` (uses installed Chrome; set `BING_TEST_BROWSER` to another Playwright channel if needed).
- Live Bing search and downloading three images were verified during development.
- The browser test uses a mocked ComfyUI host and search response. Full in-ComfyUI integration remains unverified on this machine; PyTorch and a running ComfyUI host were unavailable.
