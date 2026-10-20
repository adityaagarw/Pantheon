import { gsap, MorphSVGPlugin, SplitText } from "gsap";   // all plugins already registered
  The motion engine: timelines, easing ("expo.out", "back.out(1.6)", "elastic.out(1, .5)"),
  stagger. Plugins: DrawSVG (lines drawing on: gsap.from(path, { drawSVG: 0 })), MorphSVG
  (one shape becoming another: gsap.to(a, { morphSVG: b })), MotionPath (move along a path,
  autoRotate), SplitText (headlines by letter/word), Flip (layout changes animate),
  CustomEase/CustomBounce/CustomWiggle, ScrambleText, Text, Physics2D, Draggable + Inertia
  (drag things), PixiPlugin. A timeline is awaitable: await gsap.timeline().to(...).
  Pause GSAP with the lesson: when the page body gets the class "stage-held" call
  gsap.globalTimeline.pause(), and resume() when it goes.
