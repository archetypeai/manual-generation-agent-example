/**
 * Builds the MGA demo card's model: the compact wired mouse from the playground's assembly
 * clip (design-agents-playground, static/videos/mouse_assembly.mp4), put together in the
 * order the agent's manual gives (static/canned/mouse_assembly.json):
 *
 *   1. Take the clear plastic part and put it in the lower casing.
 *   2. Take the PCB and snap it into the lower casing.
 *   3. Make sure the cable ending is fitted into the front slot.
 *   4. Slide the upper casing's front into the rails in front of the lower casing.
 *   5. Press down the upper casing in the back.
 *   6. Fit the two screws at the back side of the mouse.
 *   7. Take a screwdriver, tighten the left screw carefully.
 *   8. Next, tighten the right screw carefully.
 *
 * What the clip shows, and the model follows:
 *  - A compact mouse, about 92 × 50 × 28 mm: a light lower casing with the lens opening, a
 *    clear lens part, a long narrow PCB with the sensor in the middle and the white rubber
 *    wheel on its encoder at the front, and a smooth upper casing split between the two
 *    buttons. The cable leaves at the front.
 *  - The screws go in from underneath: after the upper casing is pressed home the mouse is
 *    turned over, the two screws go into the corners at the back, and a thin pen-style
 *    precision screwdriver tightens them. The screws and the screwdriver start on the desk.
 *  - A plain desk, and the cable as one long run with a single bend to a plug (simplified
 *    from the clip's coiled cable).
 *
 * The turn-over can't be drawn as a rotation in an isometric still, so it is two drawings
 * of the mouse, upright and upside down, swapped while the mouse is lifted. Each moving group
 * follows keyframes (`[t, dx, dy]` screen offsets, or `[t, dx, dy, opacity]`), written into the
 * SVG as SMIL.
 *
 * Geometry is built here with the vendored Anatomy kit (MIT, wheresryan22/anatomy; see
 * kit/LICENSE). The output is one SVG styled with the console's token utilities.
 *
 *   node scripts/card-model/mga-mouse.mjs --out <svg>                  writes the SVG (the console keeps it as
 *                                                                src/lib/components/agents/models/mga-mouse.svg)
 *   node scripts/card-model/mga-mouse.mjs --out <svg> --preview <html>  also a standalone page; ?t=<s> pauses it there
 *
 * World units are millimetres: x along the mouse (front, wheel and cable, toward −x), y
 * across it, z up.
 */
import process from 'node:process';
import {
	key,
	keyed as keyedAt,
	outArg,
	previewArg,
	turning as turningAt,
	writeModel,
	ZERO
} from './common.mjs';
import * as k from './kit/iso-kit.mjs';
import * as G from './kit/lathe.mjs';
import { tubePieces, tubeSvg } from './kit/tube.mjs';

const svgAt = outArg(process.argv.slice(2));
const previewAt = previewArg(process.argv.slice(2));

const WIDTH = 560;
const HEIGHT = 340;

// --- layout ------------------------------------------------------------------------------
const DESK = { x: -104, y: -58, w: 196, d: 122, r: 6 };
const DESK_H = 4;
// Rounded fully at both ends in plan, the same outline as the shell's domes.
const BODY = { x: -46, y: -25, w: 92, d: 50, r: 24.9 };
const TRAY_H = 7;
const LENS_H = 2.6;
// The upper casing, as in the clip: one smooth shell, split down the middle between the two
// buttons. (The clip's black side trims are left out: drawn as a band they read as a ghost
// outline under the shell.)
// Its cross-section, half of it: [half-width, height above the lower casing]. Upright sides,
// rounded shoulders, a low crown across the top. The middle is this section run along the
// mouse; each end is the same curve turned round, so the shell is domed at both ends.
const SECTION = [
	[25, 0],
	[25, 6],
	[23.6, 10.4],
	[20.4, 13.6],
	[15, 15.5],
	[8, 16.4],
	[0, 16.7]
];
const TOP_Z = TRAY_H + SECTION[SECTION.length - 1][1];
const PCB = { x: -40, y: -13, w: 72, d: 26, r: 2 };
const PCB_Z = TRAY_H + LENS_H + 0.2;
const PCB_T = 1.4;
const RELIEF = { x: BODY.x - 3, y: -3, w: 8, d: 6, r: 2 };
// Upside down: the shell's rim at the bottom, then the lower casing's flat underside on top.
const FLIP_RIM = 10;
const FLIP_TOP = 17;
// All four go in from underneath, as in the clip: two at the back corners, two at the front.
const SCREWS = [
	[30, -11],
	[30, 11],
	[-34, -10],
	[-34, 10]
];
// Screw heads: they arrive standing proud and are driven down until barely above the surface.
const SCREW_SEAT = FLIP_TOP - 0.8;
const SCREW_PROUD = 1.6;
const SCREW_REST = [
	[16, 40],
	[24, 44],
	[32, 38],
	[40, 42]
];
const DRIVER = { y: 52, from: -40, shaft: 30, handle: 44 };

// Exploded offsets, world units.
const APART = {
	lens: [0, 0, 16],
	pcb: [0, 0, 28],
	relief: [-12, 0, 0],
	shell: [-26, 0, 42],
	press: [0, 0, 5]
};
const LIFT = 26;

// --- the cycle, in seconds ----------------------------------------------------------------
const PERIOD = 15;
const T = {
	lens: [0.8, 1.5],
	pcb: [1.9, 2.8],
	relief: [3.1, 3.6],
	shellIn: [4.0, 5.0],
	press: [5.2, 5.7],
	liftUp: [6.0, 6.4],
	flipDown: [6.5, 7.0],
	screws: [7.3, 8.0],
	flipUp: [13.4, 13.8],
	reset: [13.8, 14.6]
};
// The screwdriver's turn at the bench, once the mouse is upside down: `spans` are the times
// it is down on each screw. Between two screws it lifts for HOVER, moves across, then lowers for
// HOVER, so each gap between spans is more than twice HOVER and the keys stay in time order.
const HOVER = 0.15;
const DRIVES = [
	{
		in: [8.2, 8.5],
		spans: [
			[8.8, 9.3],
			[9.8, 10.3],
			[10.8, 11.3],
			[11.8, 12.3]
		],
		out: [12.5, 12.8]
	}
];

// --- camera -------------------------------------------------------------------------------
const P = k.fitProjection(
	[
		...k.boxCorners(DESK, -DESK_H, 0),
		...k.boxCorners(
			{ ...BODY, x: BODY.x + APART.shell[0] },
			0,
			TOP_Z + APART.shell[2] + APART.press[2]
		),
		[SCREWS[0][0], SCREWS[0][1], FLIP_TOP + 1.2 + 14 + DRIVER.shaft + DRIVER.handle]
	],
	WIDTH,
	HEIGHT,
	{ pad: 4, azimuth: 36 }
);
const at = (p) => k.iso(p, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (points, style) => k.dotsSvg(points, style);
const origin = at([0, 0, 0]);
const screenOf = ([x, y, z]) => {
	const p = at([x, y, z]);
	return [+(p[0] - origin[0]).toFixed(2), +(p[1] - origin[1]).toFixed(2)];
};
const alongX = (o) => G.frameOf(o, [1, 0, 0], [0, 1, 0], [0, 0, 1]);
const alongY = (o) => G.frameOf(o, [0, 1, 0], [1, 0, 0], [0, 0, 1]);
const alongZ = (o) => G.frameOf(o, [0, 0, 1], [1, 0, 0], [0, 1, 0]);

// --- keyframes --------------------------------------------------------------------------------
const lifted = screenOf([0, 0, LIFT]);

/** An upright part: apart until it goes home, then up and out with the turn-over, back apart at the reset. */
function uprightKeys(apart, [t0, t1]) {
	const off = screenOf(apart);
	return [
		key(0, off, 1),
		key(t0, off, 1),
		key(t1, ZERO, 1),
		key(T.liftUp[0], ZERO, 1),
		key(T.liftUp[1], lifted, 0),
		key(T.reset[0], off, 0),
		key(T.reset[1], off, 1),
		key(PERIOD, off, 1)
	];
}

// --- desk ---------------------------------------------------------------------------------------
function desk() {
	const out = [`<path class="iso-halo" d="${k.haloOf(DESK, -DESK_H, DESK_H, P)}"/>`];
	out.push(S(k.slabOf(DESK, -DESK_H, DESK_H, P, 4, 0.6), { tone: 'lo' }));
	// A few long grain lines, edge to edge.
	const grain = [-41, -22, -9, 7, 19, 33, 50].map((y) =>
		k.lineOnTop([DESK.x, y], [DESK.x + DESK.w, y], 0, P)
	);
	out.push(L(grain.join(''), { tone: 'faint' }));
	return out.join('');
}

// --- cable: a long run out of the front, one easy bend, ending in a slim USB-C plug ---------------------
const CABLE_X = DESK.x + 16;
const PLUG_Y = 40;
/**
 * The cable and its plug, from the cable's end at `[dx, dz]` off the seated strain relief,
 * down to the desk and over to the plug. `side` is 1 upright and −1 once the mouse is turned
 * over: the turn is about the mouse's long axis, so the cable goes over with it to the
 * other side of the desk.
 */
function cable([dx, dz], side = 1) {
	const r = 1.4;
	const z = r + 0.05;
	const y = side * PLUG_Y;
	const route = G.fillet(
		[
			[RELIEF.x - 0.2 + dx, 0, TRAY_H - 2 + dz],
			[Math.min(BODY.x - 14, RELIEF.x - 12 + dx), 0, z],
			[CABLE_X, 0, z],
			[CABLE_X, y + side * 0.2, z]
		],
		14,
		9,
		{ P, tube: r }
	);
	const out = tubePieces(route, r, P, { maxLength: 400, caps: [true, false] }).map((piece) =>
		tubeSvg(piece, { tone: 'mid' })
	);
	const plug = { x: CABLE_X - 2.4, y: side > 0 ? y : y - 10, w: 4.8, d: 10, r: 2.4 };
	out.push(S(k.slabOf(plug, 0, 3, P, 6, 0.4), { tone: 'hi' }));
	return out.join('');
}

// --- the upright mouse ---------------------------------------------------------------------------------
function tray() {
	const out = [S(k.slabOf(BODY, 0, TRAY_H, P, 8, 0.8), { tone: 'mid' })];
	out.push(L(k.planOutline(k.insetPlan(BODY, 2.2), TRAY_H, P, 8), { tone: 'lo' }));
	// The lens opening in the floor, and the bosses the screws come up into.
	out.push(L(k.planOutline({ x: -9, y: -6, w: 18, d: 12, r: 5 }, TRAY_H, P, 6), { tone: 'lo' }));
	for (const [x, y] of SCREWS)
		out.push(S(k.cylinder(x, y, 2.8, TRAY_H, 1.4, P, 18), { tone: 'mid' }));
	// The rails at the front the upper casing's lip slides under.
	for (const y of [-19, 15])
		out.push(
			S(k.slabOf({ x: BODY.x + 12, y, w: 9, d: 4, r: 1 }, TRAY_H, 1.4, P, 3), { tone: 'mid' })
		);
	return out.join('');
}

function lens() {
	// The clear lens part: drawn in the faintest ink, as a clear part reads.
	const p = { x: -8, y: -5, w: 16, d: 10, r: 4 };
	return [
		S(k.slabOf(p, TRAY_H, LENS_H, P, 6, 0.6), { tone: 'faint', crease: 'none' }),
		S(k.cylinder(0, 0, 2.6, TRAY_H + LENS_H, 1.2, P, 20), { tone: 'faint', crease: 'none' })
	].join('');
}

function pcb() {
	const top = PCB_Z + PCB_T;
	const out = [S(k.slabOf(PCB, PCB_Z, PCB_T, P, 3, 0.4), { tone: 'mid' })];
	out.push(
		D(
			k.corners(PCB, 2.6).map(([x, y]) => at([x, y, top])),
			{ size: 0.5, tone: 'lo' }
		)
	);
	// The optical sensor in the middle, over the lens part.
	out.push(S(k.slabOf({ x: -6, y: -6, w: 12, d: 12, r: 1 }, top, 2.6, P, 3, 0.4), { tone: 'mid' }));
	out.push(L(k.ring(0, 0, 2.2, top + 2.6, P, 16), { tone: 'lo' }));
	// LEDs and capacitors, a controller chip at the back.
	for (const [x, y] of [
		[-14, 7],
		[10, -8]
	])
		out.push(S(k.cylinder(x, y, 1.6, top, 3.2, P, 14), { tone: 'hi' }));
	for (const [x, y] of [
		[12, 7],
		[16, 7]
	])
		out.push(S(k.cylinder(x, y, 1.4, top, 4, P, 14), { tone: 'mid' }));
	out.push(S(k.slabOf({ x: 18, y: -7, w: 10, d: 8, r: 0.6 }, top, 1.4, P, 2), { tone: 'mid' }));
	// The wheel on its encoder at the front, under the shell's slot: white rubber, so the
	// lightest part on the board.
	out.push(
		S(k.slabOf({ x: SLOT.x - 4, y: -5, w: 3, d: 4, r: 0.6 }, top, 5, P, 2), { tone: 'mid' })
	);
	out.push(wheelSolid());
	return out.join('');
}

function relief() {
	// The cable's strain relief, which seats in the front slot.
	return S(k.slabOf(RELIEF, TRAY_H - 4, 4, P, 3, 0.4), { tone: 'mid' });
}

const SLOT = { x: BODY.x + 20, y: -3.2, w: 12, d: 6.4, r: 3.2 };
const SLOT_Z = TRAY_H + 16.4;
function shell() {
	const half = BODY.d / 2;
	const ends = [BODY.x + half, BODY.x + BODY.w - half];
	const base = TRAY_H;
	// One shell from three pieces sharing one section: a dome at each end and the run between
	// them. The pieces are drawn without their own outlines, the run without end caps, and one
	// outline goes round the whole: the shell is convex, so that outline is the hull of the lot.
	const domePaths = (x) =>
		G.solidOf(
			SECTION.map(([r, h], i) => (i > 1 ? [h, r, 1] : [h, r])),
			alongZ([x, 0, base]),
			P
		);
	const bare = (paths) => S({ ...paths, outline: '', crease: '' }, { tone: 'hi', crease: 'none' });
	const out = [bare(domePaths(ends[0])), bare(domePaths(ends[1]))];
	// The run: one quad per edge of the section, only the faces that look our way.
	const ring = [
		...SECTION,
		...SECTION.slice(0, -1)
			.reverse()
			.map(([r, h]) => [-r, h])
	];
	const view = G.viewOf(P);
	const faces = [];
	for (let i = 0; i < ring.length - 1; i++) {
		const [ya, ha] = ring[i];
		const [yb, hb] = ring[i + 1];
		let normal = [0, hb - ha, -(yb - ya)];
		const mid = [0, (ya + yb) / 2, (ha + hb) / 2 - 8];
		if (G.dot3(normal, mid) < 0) normal = G.mul3(normal, -1);
		if (G.dot3(normal, view) <= 0) continue;
		const quad = [
			[ends[0], ya, base + ha],
			[ends[1], ya, base + ha],
			[ends[1], yb, base + hb],
			[ends[0], yb, base + hb]
		].map(at);
		const band = G.toneOf(normal, P);
		const cls = band > 3 ? 'class="iso-top"' : `class="iso-shade" data-shade="${band}"`;
		faces.push(
			`<path class="iso-fill" d="${k.pathOf(quad, true)}"/><path ${cls} d="${k.pathOf(quad, true)}"/>`
		);
	}
	out.push(faces.join(''));
	const silhouette = [];
	for (const x of ends)
		for (let a = 0; a < 64; a++) {
			const t = (a / 64) * 2 * Math.PI;
			for (const [r, h] of SECTION)
				silhouette.push(at([x + r * Math.cos(t), r * Math.sin(t), base + h]));
		}
	out.push(L(k.pathOf(G.hullOf(silhouette), true), { tone: 'hi' }));
	// The split between the two buttons: along the ridge from the wheel's slot to the back, down
	// the back dome to the rim, and forward from the slot as far as the front dome faces us.
	const slot = SLOT;
	const ridge = k.lineOnTop([slot.x + slot.w, 0], [ends[1], 0], TOP_Z, P);
	const meridian = (x, dir, from, to) =>
		k.pathOf(
			SECTION.filter(([r]) => r >= from && r <= to)
				.reverse()
				.map(([r, h]) => at([x + dir * r, 0, base + h]))
		);
	out.push(L(ridge + meridian(ends[1], 1, 0, half), { tone: 'mid' }));
	// Forward from the slot's front edge, as far as the front dome faces us.
	const front = [
		at([slot.x, 0, base + 16.4]),
		...SECTION.filter(([r]) => r > ends[0] - slot.x && r <= 20.4)
			.reverse()
			.map(([r, h]) => at([ends[0] - r, 0, base + h]))
	];
	out.push(L(k.pathOf(front), { tone: 'mid', free: 'end' }));
	// The slot the wheel shows through.
	out.push(L(k.planOutline(slot, SLOT_Z, P, 4), { tone: 'mid' }));
	return out.join('');
}

// The wheel sits on the PCB, inside the mouse, and shows through the shell's slot once the
// shell is on: the same wheel drawn again over the shell, clipped to the slot and the space
// just above it.
const WHEEL_R = 7.4;
function wheelSolid() {
	const F = alongY([SLOT.x + SLOT.w / 2, -3, PCB_Z + PCB_T + 6.6]);
	return S(G.disc(0, 6, WHEEL_R, F, P), {
		tone: 'hi',
		inner: k.fadedSvg(G.ribsOf(F, 0.4, 5.6, WHEEL_R, 30, P, { fade: [0.05, 0.45], seams: true }), {
			tone: 'faint'
		})
	});
}
function wheelPeek() {
	const ring = (z) => k.ringOf(SLOT, 4).map(([x, y]) => at([x, y, z]));
	const clip = k.pathOf(G.hullOf([...ring(SLOT_Z), ...ring(SLOT_Z + 4)]), true);
	return `<clipPath id="mga-wheel-slot"><path d="${clip}"/></clipPath><g clip-path="url(#mga-wheel-slot)">${wheelSolid()}</g>`;
}

// --- the mouse turned over -------------------------------------------------------------------------------
function flipped() {
	// Upside down the upper casing is on the desk and the lower casing's flat underside is on top.
	const out = [S(k.slabOf(BODY, 0, FLIP_RIM, P, 8, 0.6), { tone: 'hi' })];
	out.push(S(k.slabOf(BODY, FLIP_RIM, FLIP_TOP - FLIP_RIM, P, 8, 0.8), { tone: 'hi' }));
	const z = FLIP_TOP;
	// The sensor window, the label, the grille and the four screw holes.
	out.push(
		S(k.slabOf({ x: -4, y: -9, w: 20, d: 14, r: 7 }, z, 0.3, P, 8), { tone: 'lo', crease: 'none' })
	);
	out.push(L(k.planOutline({ x: 0, y: -6, w: 12, d: 8, r: 4 }, z + 0.3, P, 6), { tone: 'lo' }));
	const label = { x: -27, y: -11, w: 18, d: 22, r: 1.4 };
	out.push(S(k.slabOf(label, z, 0.3, P, 3), { tone: 'mid', crease: 'none' }));
	const grille = { x: 19, y: -5, w: 8, d: 10, r: 1 };
	for (let n = 0; n < 4; n++)
		out.push(
			L(k.planOutline({ x: grille.x + n * 2, y: grille.y, w: 0.8, d: grille.d, r: 0.4 }, z, P, 2), {
				tone: 'faint'
			})
		);
	for (const [x, y] of SCREWS) out.push(L(k.ring(x, y, 2.4, z, P, 16), { tone: 'lo' }));
	return out.join('');
}

// While the screwdriver is on a screw, the screw's cross and the screwdriver's flutes turn:
// each is drawn at a few phases, and the page steps through them during `spans` of the cycle.
const TURN_FRAMES = 6;
const turning = (spans, frames) => turningAt(PERIOD, spans, frames);

function screw(x, y, z, spans) {
	const out = [S(k.cylinder(x, y, 2.2, z, 1.2, P, 18, 0.3), { tone: 'hi' })];
	const top = z + 1.2;
	const cross = (a) => {
		const [c, s] = [1.3 * Math.cos(a), 1.3 * Math.sin(a)];
		return L(
			k.lineOnTop([x - c, y - s], [x + c, y + s], top, P) +
				k.lineOnTop([x + s, y - c], [x - s, y + c], top, P),
			{
				tone: 'lo',
				free: true
			}
		);
	};
	out.push(
		turning(
			spans,
			Array.from({ length: TURN_FRAMES }, (_, i) => cross((i / TURN_FRAMES) * (Math.PI / 2)))
		)
	);
	return out.join('');
}

// The precision screwdriver: pen-thin, a fluted handle and a long shaft.
const DRIVER_PROFILE = [
	[0, 0.1],
	[2, 0.9],
	[DRIVER.shaft, 0.9],
	[DRIVER.shaft, 1.6],
	[DRIVER.shaft + 2, 2.6, 1],
	[DRIVER.shaft + DRIVER.handle - 2, 2.6, 1],
	[DRIVER.shaft + DRIVER.handle, 1.6, 1]
];
const FLUTES = 16;
const flutes = (F, phase) =>
	k.fadedSvg(
		G.ribsOf(F, DRIVER.shaft + 4, DRIVER.shaft + DRIVER.handle - 6, 2.6, FLUTES, P, {
			fade: [0.05, 0.45],
			seams: true,
			phase
		}),
		{ tone: 'lo' }
	);
const lyingDriver = () => {
	const F = alongX([DRIVER.from, DRIVER.y, 2.7]);
	return S(G.solidOf(DRIVER_PROFILE, F, P), { tone: 'hi', inner: flutes(F, 0) });
};
const standingDriver = (spans) => {
	const F = alongZ([SCREWS[0][0], SCREWS[0][1], 0]);
	const step = (2 * Math.PI) / FLUTES / TURN_FRAMES;
	return (
		S(G.solidOf(DRIVER_PROFILE, F, P), { tone: 'hi' }) +
		turning(
			spans,
			Array.from({ length: TURN_FRAMES }, (_, i) => flutes(F, i * step))
		)
	);
};

// --- keyframes for the turn-over half of the cycle ---------------------------------------------------------
const flippedKeys = [
	key(0, lifted, 0),
	key(T.flipDown[0], lifted, 0),
	key(T.flipDown[0] + 0.15, lifted, 1),
	key(T.flipDown[1], ZERO, 1),
	key(T.flipUp[0], ZERO, 1),
	key(T.flipUp[1], lifted, 0),
	key(PERIOD, lifted, 0)
];
/** A screw: on the desk, carried to stand proud over its hole, driven down while the screwdriver turns it, then away with the mouse. */
const screwKeys = ([rx, ry], [hx, hy, seat], arrive, drive, gone) => {
	const rest = screenOf([rx - hx, ry - hy, -seat]);
	const above = screenOf([0, 0, 12]);
	const proud = screenOf([0, 0, SCREW_PROUD]);
	return [
		key(0, rest, 1),
		key(arrive[0], rest, 1),
		key((arrive[0] + arrive[1]) / 2, above, 1),
		key(arrive[1], proud, 1),
		key(drive[0], proud, 1),
		key(drive[1], ZERO, 1),
		key(gone[0], ZERO, 1),
		key(gone[1], lifted, 0),
		key(T.reset[0], rest, 0),
		key(T.reset[1], rest, 1),
		key(PERIOD, rest, 1)
	];
};
// The cable is already plugged into the PCB at the start, as in the clip, and its end goes
// wherever that part goes: down with the PCB, into the front slot, up and over with the mouse,
// and back apart. A drawn tube can't bend, so the cable is drawn once for each place its end
// can be ([dx, dz] off the seated relief), and the drawings crossfade while the part moves.
const fades = (pairs) => pairs.map(([t, o]) => key(t, ZERO, o));
const CABLE_POSES = [
	// With the PCB, apart.
	[
		[APART.relief[0], APART.pcb[2]],
		1,
		fades([
			[0, 1],
			[T.pcb[0], 1],
			[T.pcb[1], 0],
			[T.reset[0], 0],
			[T.reset[1], 1],
			[PERIOD, 1]
		])
	],
	// PCB home, the relief not yet in its slot.
	[
		[APART.relief[0], 0],
		1,
		fades([
			[0, 0],
			[T.pcb[0], 0],
			[T.pcb[1], 1],
			[T.relief[0], 1],
			[T.relief[1], 0],
			[PERIOD, 0]
		])
	],
	// Seated.
	[
		[0, 0],
		1,
		fades([
			[0, 0],
			[T.relief[0], 0],
			[T.relief[1], 1],
			[T.liftUp[0], 1],
			[T.liftUp[1], 0],
			[PERIOD, 0]
		])
	],
	// Lifted upright, on the way to turning it over.
	[
		[0, LIFT],
		1,
		fades([
			[0, 0],
			[T.liftUp[0], 0],
			[T.liftUp[1], 1],
			[T.flipDown[0], 1],
			[T.flipDown[1], 0],
			[PERIOD, 0]
		])
	],
	// Upside down: the cable has gone over with the mouse, to the other side.
	[
		[0, FLIP_TOP - 5 - (TRAY_H - 2)],
		-1,
		fades([
			[0, 0],
			[T.flipDown[0], 0],
			[T.flipDown[1], 1],
			[T.flipUp[0], 1],
			[T.flipUp[1], 0],
			[PERIOD, 0]
		])
	]
];
// The strain relief rides with the PCB, then seats in the slot (step 3).
const reliefKeys = (() => {
	const apart = screenOf([APART.relief[0], 0, APART.pcb[2]]);
	const out = screenOf([APART.relief[0], 0, 0]);
	return [
		key(0, apart, 1),
		key(T.pcb[0], apart, 1),
		key(T.pcb[1], out, 1),
		key(T.relief[0], out, 1),
		key(T.relief[1], ZERO, 1),
		key(T.liftUp[0], ZERO, 1),
		key(T.liftUp[1], lifted, 0),
		key(T.reset[0], apart, 0),
		key(T.reset[1], apart, 1),
		key(PERIOD, apart, 1)
	];
})();
const lyingKeys = [
	key(0, ZERO, 1),
	...DRIVES.flatMap((d) => [
		key(d.in[0], ZERO, 1),
		key(d.in[1], ZERO, 0),
		key(d.out[0], ZERO, 0),
		key(d.out[1], ZERO, 1)
	]),
	key(PERIOD, ZERO, 1)
];
// The screw each span drives: [x, y, seat].
const TARGETS = [SCREWS.map(([x, y]) => [x, y, SCREW_SEAT])];
const standingKeys = (() => {
	// Built standing on the desk at the first bottom screw's spot. The tip meets a screw's head
	// while it still stands proud and follows it down as it turns.
	const [ox, oy] = SCREWS[0];
	const at3 = ([x, y], z) => screenOf([x - ox, y - oy, z]);
	const out = [];
	DRIVES.forEach((d, p) => {
		const targets = TARGETS[p];
		const above = (t) => at3(t, t[2] + 1.2 + SCREW_PROUD + 10);
		if (p === 0) out.push(key(0, above(targets[0]), 0));
		out.push(key(d.in[0], above(targets[0]), 0), key(d.in[1], above(targets[0]), 1));
		targets.forEach((t, j) => {
			const [down, up] = d.spans[j];
			out.push(key(down - HOVER, above(t), 1));
			out.push(key(down, at3(t, t[2] + 1.2 + SCREW_PROUD), 1));
			out.push(key(up, at3(t, t[2] + 1.2), 1));
			out.push(key(up + HOVER, above(t), 1));
		});
		const last = targets[targets.length - 1];
		out.push(key(d.out[0], above(last), 1), key(d.out[1], above(last), 0));
	});
	out.push(key(PERIOD, out[out.length - 1].slice(1, 3), 0));
	return out;
})();
const shellKeys = (() => {
	const far = screenOf([APART.shell[0], 0, APART.shell[2] + APART.press[2]]);
	const near = screenOf(APART.press);
	return [
		key(0, far, 1),
		key(T.shellIn[0], far, 1),
		key(T.shellIn[1], near, 1),
		key(T.press[0], near, 1),
		key(T.press[1], ZERO, 1),
		key(T.liftUp[0], ZERO, 1),
		key(T.liftUp[1], lifted, 0),
		key(T.reset[0], far, 0),
		key(T.reset[1], far, 1),
		key(PERIOD, far, 1)
	];
})();

const keyed = (keys, svg) => keyedAt(PERIOD, keys, svg);

// The wheel shows through the slot from the moment the shell is home, and leaves with it.
const peekKeys = [
	key(0, ZERO, 0),
	key(T.press[1], ZERO, 0),
	key(T.press[1] + 0.05, ZERO, 1),
	key(T.liftUp[0], ZERO, 1),
	key(T.liftUp[1], lifted, 0),
	key(PERIOD, lifted, 0)
];

const items = [
	desk(),
	...CABLE_POSES.map(([pose, side, keys]) => keyed(keys, cable(pose, side))),
	keyed(uprightKeys([0, 0, 0], [0, 0]), tray()),
	keyed(uprightKeys(APART.lens, T.lens), lens()),
	keyed(uprightKeys(APART.pcb, T.pcb), pcb()),
	keyed(reliefKeys, relief()),
	keyed(shellKeys, shell()),
	keyed(peekKeys, wheelPeek()),
	keyed(flippedKeys, flipped()),
	...SCREWS.map(([x, y], i) =>
		keyed(
			screwKeys(SCREW_REST[i], [x, y, SCREW_SEAT], T.screws, DRIVES[0].spans[i], T.flipUp),
			screw(x, y, SCREW_SEAT, [DRIVES[0].spans[i]])
		)
	),
	keyed(lyingKeys, lyingDriver()),
	keyed(standingKeys, standingDriver(DRIVES.flatMap((d) => d.spans)))
];

const LABEL =
	'A compact wired mouse assembled on a desk: the clear lens and the PCB drop into the lower casing, the cable seats in the front slot, the upper casing slides onto the rails and presses down, then the mouse is turned over and a precision screwdriver tightens the two screws at the back.';
writeModel({
	out: svgAt,
	width: WIDTH,
	height: HEIGHT,
	label: LABEL,
	body: items.join(''),
	fig: 'MGA',
	title: 'Mouse assembly',
	previewAt
});
