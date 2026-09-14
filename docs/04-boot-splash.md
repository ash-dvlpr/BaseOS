# 04 — Boot splash

The bootloader displays a static BaseOS logo until the frontend draws its first
frame. Ordinary boots do not run a splash renderer or write to the framebuffer.
Custom `bootlogo.bmp` artwork requires no runtime detection or setting.

## 1. Framebuffer renderer

`src/fbsplash.c` builds a static renderer with this interface:

```text
fbsplash <progress 0-100>               full-screen static logo
fbsplash <progress 0-100|-1> <message>  compact status pill overlay
```

A message selects the pill overlay; `-1` hides its progress track. Without a
message, the renderer draws the full-screen logo for bootlogo generation or
charger-mode startup feedback.
Runtime scripts call it through `baseos-splash` only for exceptional work or a
state requiring action. Option-shaped arguments fail with exit status 2 without
drawing.

The pill preserves pixels outside its status surface. Text uses the bundled
Lexend Light font and statically linked FreeType, with a bitmap-font fallback.
After drawing, `FBIOBLANK` and `FBIOPAN_DISPLAY` make the vendor display driver
scan out the framebuffer. `FBSPLASH_TEST` renders PPM files for offline checks.

## 2. Bootlogo generation

`tools/make-bootlogo.sh <target>` generates `work/<target>/bootlogo.bmp` in the
target's vendor format: 24-bit, uncompressed, bottom-up BMP at the device's
resolution. `build-image.sh` regenerates the logo and copies it onto p2 without
changing the prepared boot prefix.

### 2.1 Panel rotation

`panel_rotation_ccw` in [devices.json](../devices.json) specifies the render
rotation: `90` for RG28XX and `0` for unrotated targets. The renderer supports
`0`, `90`, `180` and `270` degrees.

`fbsplash` reads `BASEOS_PANEL_ROTATION_CCW` from `/etc/baseos-release`, renders
in upright logical coordinates, then rotates in `present()`. The RG28XX uses
640×480 logical coordinates on a 480×640 framebuffer. For pill overlays, the
logical buffer is seeded from the framebuffer to preserve surrounding artwork.
Missing or invalid rotation values default to zero.

Bootlogo generation uses the same rotation. A rotated BMP or test preview looks
sideways in an image viewer and upright on the hardware. Render with rotation
zero to inspect the artwork. `tests/test-splash-rotation.sh` checks full-screen
and pill output pixel-for-pixel across the supported angles.

### Update scope

`.bosupd` updates replace the inactive rootfs slot, including the renderer.
They do not update p2, so bootlogo changes require reflashing the image or
separately replacing the boot-resource artwork.

## 3. Exceptional states

| condition | pill copy | progress track |
|---|---|---|
| growing + formatting storage | `EXPANDING STORAGE` | boot stage 45 |
| writing + verifying a system update | `UPDATING SYSTEM` | boot stages 50 → 95 |
| system update rejected or unwritable | `UPDATE FAILED` | none |
| restoring the previous system slot | `RESTORING SYSTEM` | none |
| first frontend install | `INSTALLING FRONTEND` | boot stage 85 |
| frontend update | `UPDATING FRONTEND` | boot stage 85 |
| card unavailable | `INSERT SD CARD` | none |
| installer cannot run | `INSTALL FAILED` | none |
| no installed frontend or installer | `ADD FRONTEND TO SD CARD` | none |
| preparing the USB storage gadget | `STARTING USB STORAGE` | none |
| user storage exported over USB | `USB STORAGE: EJECT BEFORE RESTART` | none |
| USB storage cannot bind | `USB STORAGE FAILED: POWER OFF` | none |

System-update progress advances with bytes written and verified: stages 50–80
for writing, 80–92 for verification and 95 for the slot flip. Labels describe
the operation or required action and omit version numbers.

## 4. Frontend installation

BaseOS displays a static installation pill while running the frontend installer.
The indicator uses BaseOS's own renderer and does not depend on the installer's
SDL libraries or frontend graphics initialization.

## 5. Framebuffer ownership

Every splash draw is synchronous and must finish before the frontend owns the
screen. Do not run background draw loops across handoff. System updates may
redraw progress synchronously between write/verification chunks because they
run before the frontend and report measurable progress. Do not animate progress
on a timer.

After a POWER hold is accepted in charger fallback, `baseos-splash --charger-boot`
draws `fbsplash 100` once before normal initialization. Idle charging does not
render. See [charger-only boot](11-charger-only-boot.md).
