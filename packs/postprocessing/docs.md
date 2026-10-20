import { EffectComposer, RenderPass, EffectPass, BloomEffect, VignetteEffect, NoiseEffect } from "postprocessing";
  Cinematic 3D: bloom, depth of field, vignette, grain and tone mapping. Build an
  EffectComposer(s.renderer) with a RenderPass(s.scene, s.camera) and your EffectPass, and
  assign it to s.composer: the Stage then renders through it every frame.
