# Bundled models

## deeptemp-k16-3.onnx — TempoCNN tempo estimator used by `namioto.tempo`

Attribution (CC BY-NC-SA 4.0, section 3(a)):

- Title: `deeptemp-k16-3` (TempoCNN)
- Creator: Music Technology Group, Universitat Pompeu Fabra — <https://essentia.upf.edu/models.html>
  (tempo/tempocnn/deeptemp-k16-3.pb)
- License: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International —
  full text in [LICENSE-CC-BY-NC-SA-4.0.txt](LICENSE-CC-BY-NC-SA-4.0.txt)
- Disclaimer: the model is provided as-is, without any warranty; the creators do not endorse
  Namioto or this use of it
- Changes made for this distribution: converted from the TensorFlow graph
  (`deeptemp-k16-3.pb`) to ONNX with tf2onnx, and renamed to match the ONNX file

Input is a batch of normalised mel patches, `float32[batch, 40, 1]`; the output is a BPM
distribution whose argmax plus 30 gives the BPM of that patch.

Not covered by the AGPL-3.0 license of the program: the model is a separate work with its own
license, aggregated with Namioto. CC BY-NC-SA 4.0 forbids commercial use and requires
share-alike for adaptations, so a build that ships this file may not be sold, and any replacement
model has to be distributed under the same terms. Upstream also offers the models under a
proprietary license on request.
