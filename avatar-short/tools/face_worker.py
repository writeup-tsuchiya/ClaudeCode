#!/usr/bin/env python3
"""③face の実処理。**SadTalker 専用環境の中で走る**（check_face.py から呼ばれる）。

🔴 OpenCV の標準検出（Haar）で「いちばん大きい検出＝顔」としないこと（§8-4）。
   実測で、顔ではなく「顔＋胴体」を囲む誤検出のほうが大きく、それを採用した結果
   **顎から下だけが写る動画**が黙って出来上がった。

   SadTalker が同梱している RetinaFace（detection_Resnet50_Final.pth）を使う。
   同じ写真で検出1件・確度 1.000 だった。**追加のダウンロードも不要。**
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from facexlib.detection import init_detection_model


def pick_device() -> str:
    import os

    forced = os.environ.get("SADTALKER_DEVICE", "").strip().lower()
    if forced:
        return forced
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def main() -> int:
    ap = argparse.ArgumentParser(description="③face（SadTalker 環境内で実行）")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-image", default=None, help="⑤へ渡す縮小済み画像の書き出し先")
    ap.add_argument("--face-size", type=int, default=1024)
    ap.add_argument("--margin", type=float, default=0.6,
                    help="顔の幅に対する周囲の余白の割合")
    ap.add_argument("--weights-root", default="gfpgan/weights")
    ap.add_argument("--confidence", type=float, default=0.97)
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"画像が読めません: {args.image}")
    h, w = img.shape[:2]

    device = pick_device()
    print(f"[face] device={device} image={w}x{h}", flush=True)

    # RetinaFace。Haar は使わない（§8-4）。
    det = init_detection_model("retinaface_resnet50", half=False,
                               device=device, model_rootpath=args.weights_root)
    with torch.no_grad():
        bboxes = det.detect_faces(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), args.confidence)
    bboxes = np.asarray(bboxes) if bboxes is not None else np.zeros((0, 5))

    faces = []
    for b in bboxes:
        x1, y1, x2, y2, score = float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4])
        faces.append({
            "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "confidence": round(score, 4),
            "width": round(x2 - x1, 1),
            "height": round(y2 - y1, 1),
            "touches_edge": bool(x1 <= 1 or y1 <= 1 or x2 >= w - 1 or y2 >= h - 1),
        })
    faces.sort(key=lambda f: f["confidence"], reverse=True)

    result = {
        "image": args.image,
        "image_size": [w, h],
        "detector": "retinaface_resnet50 (facexlib / detection_Resnet50_Final.pth)",
        "_detector_note": "🔴 Haar は使わない。顔＋胴体を囲む誤検出のほうが大きく、"
                          "『いちばん大きい検出＝顔』とすると顎から下だけの動画が黙って出来る（§8-4）。",
        "device": device,
        "face_count": len(faces),
        "faces": faces,
        "prepared_image": None,
        "crop_box": None,
    }

    if len(faces) == 1 and args.out_image:
        f = faces[0]
        x1, y1, x2, y2 = f["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        # 正方形で切る。顔幅の (1 + 2*margin) を1辺にする。
        side = max(x2 - x1, y2 - y1) * (1 + 2 * args.margin)
        side = min(side, min(w, h))
        # 少し上を多めに残す（額と髪が切れると不自然になる）
        cy -= side * 0.06
        sx1 = int(round(max(0, min(w - side, cx - side / 2))))
        sy1 = int(round(max(0, min(h - side, cy - side / 2))))
        side_i = int(round(side))
        crop = img[sy1:sy1 + side_i, sx1:sx1 + side_i]

        # 🔴 §8-3 出力でどう見えるかで決める。
        #    最後の「元サイズへ貼り戻す」工程が画素数に比例し、全体の3分の2を占める。
        #    ワイプに 281px でしか映らないなら、それ以上の解像度で計算した分は丸ごと捨てている。
        target = int(args.face_size)
        interp = cv2.INTER_AREA if crop.shape[0] > target else cv2.INTER_CUBIC
        resized = cv2.resize(crop, (target, target), interpolation=interp)
        Path(args.out_image).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(args.out_image, resized, [cv2.IMWRITE_PNG_COMPRESSION, 3])

        result["prepared_image"] = args.out_image
        result["crop_box"] = [sx1, sy1, side_i, side_i]
        result["prepared_size"] = [target, target]
        result["original_pixels"] = w * h
        result["prepared_pixels"] = target * target
        result["pixel_ratio"] = round((target * target) / float(w * h), 4)
        # 顔が切り出し画像の中でどこにあるか（⑧verify の口元 ROI に使う）
        result["face_in_prepared"] = {
            "x1": round((x1 - sx1) / side_i, 4),
            "y1": round((y1 - sy1) / side_i, 4),
            "x2": round((x2 - sx1) / side_i, 4),
            "y2": round((y2 - sy1) / side_i, 4),
        }
        print(f"[face] 書きました: {args.out_image} {target}x{target} "
              f"(元の {result['pixel_ratio'] * 100:.1f}% の画素数)", flush=True)

    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"[face] 書きました: {args.out_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
