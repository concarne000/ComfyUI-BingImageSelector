import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const CLASS = "BingImageSelector";
const controllers = new WeakMap();

function attach(node) {
    if (controllers.has(node)) return;
    const widget = (name) => node.widgets.find((w) => w.name === name);
    const selection = widget("selection");
    if (!selection) return;
    selection.type = "converted-widget";
    selection.computeSize = () => [0, -4];
    // Keep the actual input serialized in both the workflow and execution prompt.
    selection.serializeValue = () => selection.value;
    const root = document.createElement("div");
    root.className = "bing-selector";
    root.innerHTML = `<style>
      .bing-selector {height:100%;min-height:260px;display:flex;flex-direction:column;gap:8px;padding:8px;box-sizing:border-box;background:#20242b;color:#eee;font:12px system-ui;overflow:hidden}
      .bing-selector .toolbar {display:flex;gap:6px;flex-wrap:wrap}
      .bing-selector button {border:1px solid #606775;border-radius:6px;background:#343b47;color:#eee;padding:6px 10px;cursor:pointer}
      .bing-selector button:disabled {opacity:.5;cursor:wait}
      .bing-selector .search {background:#245b85}
      .bing-selector .status {white-space:pre-wrap;line-height:1.4}
      .bing-selector .grid {display:grid;grid-template-columns:repeat(auto-fill,minmax(108px,1fr));gap:7px;overflow:auto;min-height:120px;flex:1;align-content:start}
      .bing-selector .tile {position:relative;padding:3px;border:2px solid transparent;background:#14181e;min-width:0}
      .bing-selector .tile[aria-pressed="true"] {border-color:#50c6fa;background:#173b50}
      .bing-selector .tile img {width:100%;height:88px;object-fit:contain;display:block}
      .bing-selector .badge {position:absolute;top:5px;left:5px;background:#102d41;border-radius:4px;padding:2px 5px}
      .bing-selector .size {font-size:10px;opacity:.8;padding-top:3px}
      .bing-selector .resolution {color:#b6c9d9}
    </style><div class="toolbar"><button class="search">Search images</button><button class="all">Select all</button><button class="none">Clear</button></div><div class="resolution"></div><div class="status" role="status" aria-live="polite"></div><div class="grid"></div>`;
    root.addEventListener("pointerdown", (event) => event.stopPropagation());
    root.addEventListener("wheel", (event) => event.stopPropagation());
    const grid = root.querySelector(".grid");
    const status = root.querySelector(".status");
    const searchButton = root.querySelector(".search");
    let results = null;
    let selected = [];
    let busy = false;
    let revision = 0;
    const current = () => ({ query: String(widget("search_term").value).trim(), count: Number(widget("number_of_images").value) });
    const matches = () => results && results.query === current().query && results.count === current().count;
    const dirty = () => { node.graph?.change?.(); node.setDirtyCanvas(true, true); };
    function save() {
        selection.value = JSON.stringify({ session: results?.session, selected });
        node.properties.bing_results = results;
        dirty();
    }
    function render() {
        grid.replaceChildren();
        const valid = matches();
        status.textContent = results ? `${selected.length} selected / ${results.items.length} available (requested ${results.count}).${valid ? "" : " Search settings changed; search again."}` : "Enter a search term, then click Search images. Select thumbnails before running.";
        for (const item of results?.items || []) {
            const tile = document.createElement("button");
            tile.className = "tile";
            tile.disabled = !valid || busy;
            tile.title = item.title || item.url;
            tile.setAttribute("aria-label", `Select ${item.title || "image"}`);
            tile.setAttribute("aria-pressed", String(selected.includes(item.id)));
            const image = document.createElement("img");
            image.loading = "lazy";
            image.alt = item.title || "Bing image result";
            image.src = api.apiURL(`/bing_image_selector/thumbnail/${encodeURIComponent(results.session)}/${encodeURIComponent(item.id)}`);
            image.onerror = () => { image.alt = "Preview unavailable — search again"; };
            const badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = selected.includes(item.id) ? String(selected.indexOf(item.id) + 1) : "○";
            const size = document.createElement("div");
            size.className = "size";
            size.textContent = `${item.width} × ${item.height}`;
            tile.append(image, badge, size);
            tile.onclick = () => {
                selected = selected.includes(item.id) ? selected.filter((id) => id !== item.id) : [...selected, item.id];
                save(); render();
            };
            grid.append(tile);
        }
    }
    function resolution() {
        let width = Number(widget("width").value), height = Number(widget("height").value);
        if (widget("sizing_mode").value === "megapixels") {
            const scale = Math.sqrt(Number(widget("megapixels").value) * 1e6 / (width * height));
            width *= scale; height *= scale;
        }
        const interval = Number(widget("resolution_interval").value);
        width = Math.max(interval, Math.floor(width / interval + .5) * interval);
        height = Math.max(interval, Math.floor(height / interval + .5) * interval);
        root.querySelector(".resolution").textContent = `Output: ${width} × ${height} · ${(width * height / 1e6).toFixed(2)} MP · multiples of ${interval}`;
    }
    for (const name of ["search_term", "number_of_images", "width", "height", "sizing_mode", "megapixels", "resolution_interval"]) {
        const w = widget(name), original = w.callback;
        w.callback = function (...args) {
            const returned = original?.apply(this, args);
            if (name === "search_term" || name === "number_of_images") {
                revision++;
                render();
            }
            resolution();
            return returned;
        };
    }
    root.querySelector(".all").onclick = () => {
        if (matches() && !busy) { selected = results.items.map((item) => item.id); save(); render(); }
    };
    root.querySelector(".none").onclick = () => { if (!busy) { selected = []; save(); render(); } };
    searchButton.onclick = async () => {
        if (busy) return;
        // Values generated by upstream nodes do not exist before workflow execution.
        if (node.inputs?.some((input) => ["search_term", "number_of_images"].includes(input.name) && input.link != null)) {
            status.textContent = "For pre-run search, enter the search term and count directly in this node (disconnect upstream inputs).";
            return;
        }
        busy = true;
        searchButton.disabled = true;
        render();
        status.textContent = "Searching Bing and preparing thumbnails…";
        const ticket = ++revision;
        try {
            const response = await api.fetchApi("/bing_image_selector/search", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(current()),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Search failed (${response.status})`);
            if (ticket !== revision) throw new Error("Search settings changed during the search. Click Search images again.");
            results = data;
            selected = [];
            save();
            busy = false;
            render();
        } catch (error) {
            busy = false;
            render();
            status.textContent = error.message;
        } finally {
            busy = false;
            searchButton.disabled = false;
        }
    };
    const restore = () => {
        results = node.properties?.bing_results || null;
        try { selected = JSON.parse(selection.value).selected || []; } catch { selected = []; }
        if (!Array.isArray(selected)) selected = [];
        render(); resolution();
    };
    const dom = node.addDOMWidget("bing_gallery", "bing_gallery", root, { serialize: false, hideOnZoom: false });
    dom.computeSize = () => [360, 340];
    node.setSize([Math.max(400, node.size[0]), Math.max(650, node.size[1])]);
    controllers.set(node, { restore });
    restore();
}

app.registerExtension({
    name: "BingImageSelector.Preview",
    nodeCreated(node) { if (node.comfyClass === CLASS) attach(node); },
    loadedGraphNode(node) { if (node.comfyClass === CLASS) controllers.get(node)?.restore(); },
});
