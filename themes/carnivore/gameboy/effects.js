// Game Boy theme - DMG-01 shell plastic: moulded grain with a slow sheen.
//
// WHAT THIS CANVAS IS. The harness mounts it at `position: fixed; z-index: -1`
// behind the whole document, so it is the surface the chrome sits on, not the
// terminal. Under this theme the chrome is the CONSOLE and the terminal is the
// SCREEN, so what belongs here is hardware: the fine injection-moulded grain of
// a DMG-01's grey case and the soft highlight that travels across it when the
// unit is tilted under a light. The four-shade LCD stays where a real one is,
// inside the terminal viewport, painted from the manifest's `xterm` block.
//
// It used to paint an LCD here instead: the same lattice read as a dot-matrix
// pixel grid and the same travelling band as passive-matrix response ghosting,
// over a full-viewport `#9bbc0f`. That is what made the entire interface look
// like the screen. The geometry survived the rework because a fine two-axis
// grain and a slow diagonal highlight are what both readings need; only the
// colours, which now come from the plastic tokens, and the intent changed.
//
// Colours come from the theme's own cssVars; nothing here is hardcoded beyond
// the fallbacks that keep the draw loop total if a var is missing.
//
// Public API: init({ themeContext }), destroy(), getStatus().

import { createEffect, readVar, rgbTriple } from './effects-base.js';

/** Moulded grain pitch in CSS pixels. Case texture, read at arm's length. */
const CELL_PITCH = 4;

/** Opacity of a single grain line. Texture, not a visible grid. */
const CELL_ALPHA = 0.05;

/** Milliseconds for the sheen to cross the case once, diagonally. */
const SHEEN_PERIOD_MS = 31000;

/** Peak opacity of the sheen band itself. */
const SHEEN_ALPHA = 0.055;

/** Sheen band width as a fraction of the case diagonal. */
const SHEEN_BAND_FRACTION = 0.35;

/**
 * Build the repeating grain pattern once: one faint line per cell edge, the
 * way a moulded plastic face catches light along its texture. Two fills per
 * frame beats thousands of tiny rects.
 * @param {CanvasRenderingContext2D} ctx Destination context, used only as the
 *   pattern factory
 * @param {string} lineColour rgba() string for the grain lines
 * @returns {CanvasPattern|null} Repeating pattern, or null if unavailable
 */
function buildGrainPattern(ctx, lineColour) {
    const tile = document.createElement('canvas');
    tile.width = CELL_PITCH;
    tile.height = CELL_PITCH;
    const tctx = tile.getContext('2d');
    if (!tctx) return null;
    tctx.fillStyle = lineColour;
    tctx.fillRect(0, 0, 1, CELL_PITCH);
    tctx.fillRect(0, 0, CELL_PITCH, 1);
    return ctx.createPattern(tile, 'repeat');
}

/**
 * Resolve the palette and rebuild the grain pattern. Runs on init and on
 * every resize.
 * @param {CanvasRenderingContext2D} ctx Canvas 2D context, already DPR-scaled
 * @param {object} _env Viewport environment (unused; the pattern is size-free)
 * @returns {{pattern: CanvasPattern|null, bg: string, ink: string}} Draw state
 */
function setup(ctx, _env) {
    const bg = readVar('--color-bg-page', '#c2bcb7');
    const ink = readVar('--color-fg', '#221f26');
    const inkTriple = rgbTriple(ink, '34, 31, 38');
    return {
        pattern: buildGrainPattern(ctx, `rgba(${inkTriple}, ${CELL_ALPHA})`),
        bg,
        ink: inkTriple,
    };
}

/**
 * Paint the base case colour and its grain. Shared by the animated frame and
 * the reduced-motion static frame, which differ only by the sheen.
 * @param {CanvasRenderingContext2D} ctx Canvas 2D context
 * @param {{width: number, height: number}} env Viewport environment
 * @param {{pattern: CanvasPattern|null, bg: string}} state Draw state
 * @returns {void}
 */
function paintBase(ctx, env, state) {
    ctx.fillStyle = state.bg;
    ctx.fillRect(0, 0, env.width, env.height);
    if (state.pattern) {
        ctx.fillStyle = state.pattern;
        ctx.fillRect(0, 0, env.width, env.height);
    }
}

/**
 * Draw one frame: the grain base plus a diagonal sheen that crawls corner to
 * corner, standing in for light moving across the case.
 * @param {CanvasRenderingContext2D} ctx Canvas 2D context
 * @param {{width: number, height: number}} env Viewport environment
 * @param {{pattern: CanvasPattern|null, bg: string, ink: string}} state Draw state
 * @param {number} t Milliseconds elapsed since mount
 * @returns {void}
 */
function draw(ctx, env, state, t) {
    paintBase(ctx, env, state);

    const diag = Math.hypot(env.width, env.height);
    const bandW = diag * SHEEN_BAND_FRACTION;
    const travel = diag + bandW;
    const p = (t % SHEEN_PERIOD_MS) / SHEEN_PERIOD_MS;
    const offset = p * travel - bandW;

    // A band perpendicular to the case diagonal, translated along it.
    const ux = env.width / diag;
    const uy = env.height / diag;
    const x0 = ux * offset;
    const y0 = uy * offset;
    const x1 = ux * (offset + bandW);
    const y1 = uy * (offset + bandW);

    const grad = ctx.createLinearGradient(x0, y0, x1, y1);
    grad.addColorStop(0, `rgba(${state.ink}, 0)`);
    grad.addColorStop(0.5, `rgba(${state.ink}, ${SHEEN_ALPHA})`);
    grad.addColorStop(1, `rgba(${state.ink}, 0)`);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, env.width, env.height);
}

/**
 * Reduced-motion frame: the grain texture with no sheen at all.
 * @param {CanvasRenderingContext2D} ctx Canvas 2D context
 * @param {{width: number, height: number}} env Viewport environment
 * @param {{pattern: CanvasPattern|null, bg: string}} state Draw state
 * @returns {void}
 */
function staticFrame(ctx, env, state) {
    paintBase(ctx, env, state);
}

const effect = createEffect({
    id: 'gameboy',
    // 15fps: the sheen crawls the case over 31 seconds, well above the
    // perceptual floor for that speed, and keeps the mobile budget cheap.
    fps: 15,
    fpsMobile: 10,
    background: '#c2bcb7',
    setup,
    draw,
    staticFrame,
});

export const init = effect.init;
export const destroy = effect.destroy;
export const getStatus = effect.getStatus;
export default effect;
