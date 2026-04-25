"""ComfyUI 生成 PNG からポジティブ／ネガティブプロンプトを抽出する。

ComfyUI は PNG の tEXt チャンクに以下のキーで JSON を埋め込む:
- prompt   : API 形式のノードグラフ ( {node_id: {class_type, inputs, ...}} )
- workflow : エディタ UI 状態 (補助)

抽出ロジック:
1. PIL.Image で画像を開き、img.text / img.info の "prompt" を取り出す
2. KSampler 系ノードの inputs.positive / inputs.negative の参照先 ([node_id, idx]) を辿る
3. 中継ノード (ConditioningConcat 等) は再帰的に CLIPTextEncode まで遡る
4. 解釈失敗時は全 CLIPTextEncode の text を positive にフォールバック連結
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

# KSampler 系として扱う class_type
KSAMPLER_TYPES = {
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustom",
    "SamplerCustomAdvanced",
    "KSamplerSelect",
}

# CLIPTextEncode 系
CLIP_ENCODE_TYPES = {
    "CLIPTextEncode",
    "CLIPTextEncodeSDXL",
    "CLIPTextEncodeSDXLRefiner",
    "BNK_CLIPTextEncodeAdvanced",
    "smZ CLIPTextEncode",
}

# CLIPTextEncode を経由しない可能性のある中継ノード (positive/negative 端子から辿る)
# これら以外でも inputs に conditioning らしきキーが見つかれば追跡する。


@dataclass
class PromptExtraction:
    positive: str | None
    negative: str | None
    raw_prompt_json: str | None  # 元 JSON 文字列 (デバッグ/再解析用)
    image_size: tuple[int, int] | None


def extract_from_file(path: str | Path) -> PromptExtraction:
    """PNG ファイルからプロンプト情報を抽出する。

    画像が開けない、ComfyUI 形式でないなどの場合は None フィールドで返す。
    """
    p = Path(path)
    try:
        with Image.open(p) as img:
            size = img.size  # (w, h)
            text_data = _collect_text_chunks(img)
    except Exception as e:
        log.warning("画像を開けません: %s (%s)", p, e)
        return PromptExtraction(None, None, None, None)

    raw = text_data.get("prompt")
    if not raw:
        return PromptExtraction(None, None, None, size)

    try:
        graph = json.loads(raw)
    except json.JSONDecodeError as e:
        log.debug("prompt JSON のパース失敗 (%s): %s", p, e)
        return PromptExtraction(None, None, raw, size)

    if not isinstance(graph, dict):
        return PromptExtraction(None, None, raw, size)

    pos, neg = _extract_pos_neg(graph)
    return PromptExtraction(positive=pos, negative=neg, raw_prompt_json=raw, image_size=size)


def _collect_text_chunks(img: Image.Image) -> dict[str, str]:
    """PNG の tEXt / iTXt を辞書化する。"""
    out: dict[str, str] = {}
    # PIL は PngImageFile.text に dict[str, str] を持つ
    text_attr = getattr(img, "text", None)
    if isinstance(text_attr, dict):
        out.update({k: str(v) for k, v in text_attr.items()})
    # info 側にも入っていることがあるのでマージ
    info = getattr(img, "info", None)
    if isinstance(info, dict):
        for k, v in info.items():
            if isinstance(v, (str, bytes)) and k not in out:
                out[k] = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
    return out


def _extract_pos_neg(graph: dict[str, Any]) -> tuple[str | None, str | None]:
    """ComfyUI の API 形式グラフから positive/negative を抽出する。"""
    # まず KSampler 系を探す
    samplers = [
        (nid, node) for nid, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") in KSAMPLER_TYPES
    ]

    if samplers:
        pos_texts: list[str] = []
        neg_texts: list[str] = []
        for _nid, sampler in samplers:
            inputs = sampler.get("inputs", {}) or {}
            pos_ref = inputs.get("positive")
            neg_ref = inputs.get("negative")
            for ref, bucket in ((pos_ref, pos_texts), (neg_ref, neg_texts)):
                text = _resolve_text(graph, ref)
                if text:
                    bucket.append(text)
        pos = _join_unique(pos_texts) or None
        neg = _join_unique(neg_texts) or None
        if pos or neg:
            return pos, neg

    # フォールバック: 全 CLIPTextEncode のテキストを positive に連結
    fallback: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in CLIP_ENCODE_TYPES:
            text = _node_clip_text(node)
            if text:
                fallback.append(text)
    return (_join_unique(fallback) or None, None)


def _resolve_text(graph: dict[str, Any], ref: Any, depth: int = 0) -> str | None:
    """KSampler の positive/negative 参照を辿って CLIPTextEncode のテキストに到達する。

    ref は通常 [node_id, output_index] の 2 要素リスト。
    """
    if depth > 16 or ref is None:
        return None
    if not (isinstance(ref, list) and len(ref) >= 1):
        return None

    node_id = str(ref[0])
    node = graph.get(node_id) or graph.get(int(node_id) if str(node_id).isdigit() else node_id)
    if not isinstance(node, dict):
        return None

    class_type = node.get("class_type")
    if class_type in CLIP_ENCODE_TYPES:
        return _node_clip_text(node)

    # 中継ノードの場合: inputs を全部見て、conditioning らしきリスト参照を追う
    inputs = node.get("inputs", {}) or {}
    collected: list[str] = []
    for k, v in inputs.items():
        # 参照は [node_id, slot] 形式
        if isinstance(v, list) and len(v) >= 1 and isinstance(v[0], (str, int)):
            text = _resolve_text(graph, v, depth + 1)
            if text:
                collected.append(text)
        # 直接 text フィールドを持つ中継ノードも稀にある
        elif k == "text" and isinstance(v, str) and v.strip():
            collected.append(v)

    return _join_unique(collected) or None


def _node_clip_text(node: dict[str, Any]) -> str | None:
    """CLIPTextEncode 系ノードからテキストを取り出す。

    通常は inputs.text。SDXL 系は text_g / text_l がある。
    """
    inputs = node.get("inputs", {}) or {}
    parts: list[str] = []
    for key in ("text", "text_g", "text_l"):
        v = inputs.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return _join_unique(parts) or None


def _join_unique(items: list[str]) -> str:
    """順序を保ちつつ重複を排した連結 (区切りは改行 2 つ)。"""
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        s = s.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "\n\n".join(out)
