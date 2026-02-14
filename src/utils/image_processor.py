"""
Bulk image processing: crop, compress, convert.

Uses Pillow for all operations. Designed to process local files
from the project's media/ folder.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Tuple, List

from loguru import logger


@dataclass
class ImageProcessingResult:
    """Result of processing a single image."""
    source: Path
    output: Path
    success: bool
    original_size: int = 0
    output_size: int = 0
    original_dimensions: Tuple[int, int] = (0, 0)
    output_dimensions: Tuple[int, int] = (0, 0)
    error: Optional[str] = None


@dataclass
class BatchProcessingStats:
    """Aggregate statistics for a batch operation."""
    total: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    total_input_bytes: int = 0
    total_output_bytes: int = 0
    results: List[ImageProcessingResult] = field(default_factory=list)

    @property
    def saved_bytes(self) -> int:
        return self.total_input_bytes - self.total_output_bytes

    @property
    def compression_ratio(self) -> float:
        if self.total_input_bytes == 0:
            return 0.0
        return (1 - self.total_output_bytes / self.total_input_bytes) * 100


class ImageProcessor:
    """
    Core image processing engine.

    All methods are synchronous since Pillow operations are CPU-bound.
    """

    SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}

    def __init__(self):
        try:
            from PIL import Image, ImageOps, ImageDraw, ImageFont
            self._Image = Image
            self._ImageOps = ImageOps
            self._ImageDraw = ImageDraw
            self._ImageFont = ImageFont
        except ImportError:
            raise ImportError("Pillow is required: pip install Pillow")

    def crop_image(
        self,
        source: Path,
        output: Path,
        width: Optional[int] = None,
        height: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        strategy: str = "center",
        gradient_overlay: float = 0.0,
        gradient_height: float = 0.5,
        watermark_text: Optional[str] = None,
        watermark_image: Optional[Path] = None,
        watermark_position: str = "bottom-right",
        watermark_opacity: float = 0.3,
        watermark_size: Optional[int] = None,
        watermark_color: str = "#FFFFFF",
        watermark_scale: float = 0.2,
    ) -> ImageProcessingResult:
        """Crop a single image to size or aspect ratio, with optional gradient and watermark."""
        try:
            img = self._Image.open(source)
            img = self._auto_orient(img)
            orig_w, orig_h = img.size
            original_size = source.stat().st_size

            target_w, target_h = self._compute_target_dimensions(
                orig_w, orig_h, width, height, aspect_ratio
            )

            box = self._compute_crop_box(orig_w, orig_h, target_w, target_h, strategy)
            cropped = img.crop(box)

            if cropped.size[0] != target_w or cropped.size[1] != target_h:
                cropped = cropped.resize((target_w, target_h), self._Image.LANCZOS)

            # Convert to RGBA for compositing
            if cropped.mode != "RGBA":
                cropped = cropped.convert("RGBA")

            # Apply gradient overlay
            if gradient_overlay > 0:
                cropped = self._apply_gradient_overlay(cropped, gradient_overlay, gradient_height)

            # Apply watermark
            if watermark_text:
                cropped = self._apply_watermark_text(
                    cropped, watermark_text, watermark_position,
                    watermark_size, watermark_color, watermark_opacity,
                )
            elif watermark_image:
                cropped = self._apply_watermark_image(
                    cropped, watermark_image, watermark_position,
                    watermark_opacity, watermark_scale,
                )

            output.parent.mkdir(parents=True, exist_ok=True)
            self._save_image(cropped, output, quality=95)

            output_size = output.stat().st_size
            logger.debug(f"Cropped {source.name}: {orig_w}x{orig_h} -> {target_w}x{target_h}")

            return ImageProcessingResult(
                source=source, output=output, success=True,
                original_size=original_size, output_size=output_size,
                original_dimensions=(orig_w, orig_h),
                output_dimensions=(target_w, target_h),
            )
        except Exception as e:
            logger.warning(f"Failed to crop {source.name}: {e}")
            return ImageProcessingResult(
                source=source, output=output, success=False, error=str(e)
            )

    def compress_image(
        self,
        source: Path,
        output: Path,
        quality: int = 80,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None,
        strip_metadata: bool = True,
    ) -> ImageProcessingResult:
        """Compress a single image for web."""
        try:
            img = self._Image.open(source)
            img = self._auto_orient(img)
            orig_w, orig_h = img.size
            original_size = source.stat().st_size

            if max_width or max_height:
                img.thumbnail(
                    (max_width or img.size[0], max_height or img.size[1]),
                    self._Image.LANCZOS,
                )

            output.parent.mkdir(parents=True, exist_ok=True)

            save_kwargs = {"quality": quality, "optimize": True}
            if not strip_metadata and "exif" in img.info:
                save_kwargs["exif"] = img.info["exif"]
            if "icc_profile" in img.info:
                save_kwargs["icc_profile"] = img.info["icc_profile"]

            fmt = output.suffix.lower()
            if fmt in ('.jpg', '.jpeg'):
                img = self._ensure_rgb(img)
                img.save(output, format="JPEG", **save_kwargs)
            elif fmt == '.webp':
                img.save(output, format="WEBP", **save_kwargs)
            elif fmt == '.png':
                img.save(output, format="PNG", optimize=True)
            else:
                img.save(output, **save_kwargs)

            out_w, out_h = img.size
            output_size = output.stat().st_size
            logger.debug(
                f"Compressed {source.name}: {original_size / 1024:.0f}KB -> "
                f"{output_size / 1024:.0f}KB ({self._pct(original_size, output_size)})"
            )

            return ImageProcessingResult(
                source=source, output=output, success=True,
                original_size=original_size, output_size=output_size,
                original_dimensions=(orig_w, orig_h),
                output_dimensions=(out_w, out_h),
            )
        except Exception as e:
            logger.warning(f"Failed to compress {source.name}: {e}")
            return ImageProcessingResult(
                source=source, output=output, success=False, error=str(e)
            )

    def convert_image(
        self,
        source: Path,
        output: Path,
        target_format: str = "webp",
        quality: int = 85,
        strip_metadata: bool = True,
    ) -> ImageProcessingResult:
        """Convert a single image to a different format."""
        try:
            img = self._Image.open(source)
            img = self._auto_orient(img)
            orig_w, orig_h = img.size
            original_size = source.stat().st_size

            format_map = {"webp": "WEBP", "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG"}
            pil_format = format_map.get(target_format.lower(), target_format.upper())

            if pil_format == "JPEG":
                img = self._ensure_rgb(img)

            output.parent.mkdir(parents=True, exist_ok=True)

            save_kwargs: dict = {}
            if not strip_metadata and "exif" in img.info:
                save_kwargs["exif"] = img.info["exif"]
            if "icc_profile" in img.info:
                save_kwargs["icc_profile"] = img.info["icc_profile"]

            if pil_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True

            img.save(output, format=pil_format, **save_kwargs)

            output_size = output.stat().st_size
            logger.debug(
                f"Converted {source.name} -> {output.name}: "
                f"{original_size / 1024:.0f}KB -> {output_size / 1024:.0f}KB"
            )

            return ImageProcessingResult(
                source=source, output=output, success=True,
                original_size=original_size, output_size=output_size,
                original_dimensions=(orig_w, orig_h),
                output_dimensions=(orig_w, orig_h),
            )
        except Exception as e:
            logger.warning(f"Failed to convert {source.name}: {e}")
            return ImageProcessingResult(
                source=source, output=output, success=False, error=str(e)
            )

    def collect_files(
        self,
        input_dir: Path,
        glob_pattern: str = "*.{jpg,jpeg,png,webp}",
        recursive: bool = False,
    ) -> List[Path]:
        """
        Collect image files from a directory.
        Expands brace patterns (*.{jpg,png}) into multiple globs.
        """
        patterns = self._expand_brace_pattern(glob_pattern)
        glob_fn = input_dir.rglob if recursive else input_dir.glob

        files: set[Path] = set()
        for pattern in patterns:
            files.update(f for f in glob_fn(pattern) if f.is_file())

        return sorted(files)

    # === Internal helpers ===

    @staticmethod
    def parse_aspect_ratio(ratio_str: str) -> Tuple[int, int]:
        """Parse '16:9' -> (16, 9). Raises ValueError on bad input."""
        parts = ratio_str.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid aspect ratio format: '{ratio_str}'. Use 'W:H' (e.g. 16:9)")
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError(f"Aspect ratio must be integers: '{ratio_str}'")
        if w <= 0 or h <= 0:
            raise ValueError(f"Aspect ratio values must be positive: '{ratio_str}'")
        return w, h

    def _compute_target_dimensions(
        self,
        orig_w: int,
        orig_h: int,
        width: Optional[int],
        height: Optional[int],
        aspect_ratio: Optional[str],
    ) -> Tuple[int, int]:
        """Compute target dimensions from width/height/aspect_ratio combination."""
        if aspect_ratio:
            ar_w, ar_h = self.parse_aspect_ratio(aspect_ratio)
            if width and height:
                return width, height
            elif width:
                return width, int(width * ar_h / ar_w)
            elif height:
                return int(height * ar_w / ar_h), height
            else:
                # Crop to aspect ratio preserving maximum area
                if orig_w / orig_h > ar_w / ar_h:
                    target_h = orig_h
                    target_w = int(orig_h * ar_w / ar_h)
                else:
                    target_w = orig_w
                    target_h = int(orig_w * ar_h / ar_w)
                return target_w, target_h
        else:
            if not width or not height:
                raise ValueError("Must specify both --width and --height, or use --aspect-ratio")
            return width, height

    @staticmethod
    def _compute_crop_box(
        img_w: int, img_h: int,
        target_w: int, target_h: int,
        strategy: str,
    ) -> Tuple[int, int, int, int]:
        """Compute (left, upper, right, lower) crop box."""
        crop_w = min(target_w, img_w)
        crop_h = min(target_h, img_h)

        if strategy == "center":
            left = (img_w - crop_w) // 2
            upper = (img_h - crop_h) // 2
        elif strategy == "top":
            left = (img_w - crop_w) // 2
            upper = 0
        elif strategy == "bottom":
            left = (img_w - crop_w) // 2
            upper = img_h - crop_h
        elif strategy == "left":
            left = 0
            upper = (img_h - crop_h) // 2
        elif strategy == "right":
            left = img_w - crop_w
            upper = (img_h - crop_h) // 2
        else:
            raise ValueError(f"Unknown crop strategy: '{strategy}'. Use: center, top, bottom, left, right")

        return left, upper, left + crop_w, upper + crop_h

    def _apply_gradient_overlay(self, img, opacity: float, height_pct: float = 0.5):
        """Apply a bottom-to-top dark gradient overlay."""
        w, h = img.size
        overlay = self._Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gradient_h = max(1, int(h * height_pct))
        max_alpha = int(255 * opacity)
        draw = self._ImageDraw.Draw(overlay)

        for y in range(gradient_h):
            # y=0 is the top of gradient zone; gradient zone is at the bottom of the image
            row_y = h - gradient_h + y
            # y=0 at top of gradient (transparent), y=gradient_h-1 at bottom (opaque)
            alpha = int(max_alpha * y / gradient_h)
            draw.line([(0, row_y), (w, row_y)], fill=(0, 0, 0, alpha))

        return self._Image.alpha_composite(img, overlay)

    def _apply_watermark_text(
        self, img, text: str, position: str = "bottom-right",
        font_size: Optional[int] = None, color: str = "#FFFFFF", opacity: float = 0.3,
    ):
        """Apply a text watermark on the image."""
        w, h = img.size
        overlay = self._Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = self._ImageDraw.Draw(overlay)

        if font_size is None:
            font_size = max(12, int(w * 0.04))

        try:
            font = self._ImageFont.truetype("arial.ttf", font_size)
        except (OSError, IOError):
            font = self._ImageFont.load_default()

        # Parse hex color
        color = color.lstrip("#")
        r, g, b = int(color[:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        alpha = int(255 * opacity)

        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        margin = int(min(w, h) * 0.03)
        x, y = self._compute_position(w, h, text_w, text_h, position, margin)

        draw.text((x, y), text, font=font, fill=(r, g, b, alpha))
        return self._Image.alpha_composite(img, overlay)

    def _apply_watermark_image(
        self, img, watermark_path: Path, position: str = "bottom-right",
        opacity: float = 0.3, scale: float = 0.2,
    ):
        """Apply an image watermark on the image."""
        w, h = img.size
        wm = self._Image.open(watermark_path).convert("RGBA")

        # Scale watermark relative to main image width
        wm_w = max(1, int(w * scale))
        wm_h = max(1, int(wm.size[1] * wm_w / wm.size[0]))
        wm = wm.resize((wm_w, wm_h), self._Image.LANCZOS)

        # Apply opacity
        wm_data = wm.split()
        alpha_channel = wm_data[3].point(lambda a: int(a * opacity))
        wm.putalpha(alpha_channel)

        margin = int(min(w, h) * 0.03)
        x, y = self._compute_position(w, h, wm_w, wm_h, position, margin)

        overlay = self._Image.new("RGBA", (w, h), (0, 0, 0, 0))
        overlay.paste(wm, (x, y))
        return self._Image.alpha_composite(img, overlay)

    @staticmethod
    def _compute_position(
        img_w: int, img_h: int, obj_w: int, obj_h: int,
        position: str, margin: int = 10,
    ) -> Tuple[int, int]:
        """Compute (x, y) for placing an object on the image."""
        positions = {
            "center": ((img_w - obj_w) // 2, (img_h - obj_h) // 2),
            "top-left": (margin, margin),
            "top-right": (img_w - obj_w - margin, margin),
            "bottom-left": (margin, img_h - obj_h - margin),
            "bottom-right": (img_w - obj_w - margin, img_h - obj_h - margin),
            "bottom-center": ((img_w - obj_w) // 2, img_h - obj_h - margin),
        }
        if position not in positions:
            raise ValueError(f"Unknown position: '{position}'. Use: {', '.join(positions)}")
        return positions[position]

    def _auto_orient(self, img):
        """Apply EXIF orientation and strip orientation tag."""
        return self._ImageOps.exif_transpose(img)

    def _ensure_rgb(self, img):
        """Convert RGBA/P to RGB with white background (for JPEG)."""
        if img.mode in ("RGBA", "LA"):
            background = self._Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        elif img.mode == "P":
            img = img.convert("RGBA")
            background = self._Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        elif img.mode != "RGB":
            return img.convert("RGB")
        return img

    def _save_image(self, img, output: Path, quality: int = 95):
        """Save image, detecting format from extension."""
        fmt = output.suffix.lower()
        if fmt in ('.jpg', '.jpeg'):
            img = self._ensure_rgb(img)
            img.save(output, format="JPEG", quality=quality, optimize=True)
        elif fmt == '.webp':
            img.save(output, format="WEBP", quality=quality, optimize=True)
        elif fmt == '.png':
            img.save(output, format="PNG", optimize=True)
        else:
            img.save(output)

    @staticmethod
    def _expand_brace_pattern(pattern: str) -> List[str]:
        """Expand '*.{jpg,png,webp}' into ['*.jpg', '*.png', '*.webp']."""
        match = re.match(r'^(.*)\{(.+)\}(.*)$', pattern)
        if match:
            prefix, alternatives, suffix = match.groups()
            return [f"{prefix}{alt.strip()}{suffix}" for alt in alternatives.split(",")]
        return [pattern]

    @staticmethod
    def _pct(original: int, compressed: int) -> str:
        if original == 0:
            return "0%"
        ratio = (1 - compressed / original) * 100
        return f"-{ratio:.1f}%"
