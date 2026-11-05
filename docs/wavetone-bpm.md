# WaveTone BPM estimator

## Reverse-engineering basis

The reference is WaveTone 2.73's `data/awlib.dll`, SHA-256
`a40749620a0a31c75a3df9ae5aff0d51b3b9f2fe29739b1f3c1e5ce38014a8b2`, opened and analyzed in
`/home/quy/ghidra-cli-projects/awlib.rep` with Ghidra. The relevant exported routine is
`dfttempo_awd` at `0x1000b9b0` (2,205 bytes). Ghidra's decompilation shows:

- A 4096-entry sine/cosine lookup table, with a 12-bit wrapped phase index.
- Coarse mode (`param_5 == 0`) scans 180 candidates: 60 through 239 BPM in 1 BPM steps.
- Fine mode scans 101 candidates centered on the coarse winner, from -0.50 through +0.50 BPM
  in 0.01 BPM steps.
- For each candidate, it accumulates two complex correlations. The phase increment corresponds
  to twice the candidate BPM; the full phase therefore measures 2x BPM and the integer half-phase
  measures BPM. Their magnitudes are added with equal weight.
- Candidate scores are normalized after the maximum is chosen. This scaling does not affect which
  candidate wins.

The constants in the binary confirm 60 BPM as the coarse lower bound, 0.01 BPM as the fine step,
4096 as the phase-table size, and 60,000 as the unit used in the phase increment. The routine
accepts an integer signal, its length, and its sample rate; it does not extract an audio envelope
itself. Therefore, the caller's exact signal preparation is a separate part of WaveTone behavior.

## Python implementation

`namioto.analysis.bpm.wavetone.score` mirrors the phase-table DFT using NumPy. It processes candidates
in small blocks to bound temporary memory, uses the same 4096 phase quantization, and gives the BPM
and 2x BPM correlations equal weight. The coarse and fine scans match the recovered ranges.

The current audio adapter creates a mono peak-volume envelope from 1024-sample frames and takes
the absolute difference between adjacent frame peaks. Ghidra analysis of `dfttempo_awd` confirms
that the routine consumes a sampled integer signal, but this DLL analysis does not yet establish
that WaveTone's caller constructs this exact envelope. Mono conversion and input scaling can also
affect the result. Those are remaining compatibility questions, distinct from the DFT scoring
logic now reproduced here.

On `/home/quy/Music/nicokara/ideal/vocal.wav`, the former 0.5 second-harmonic weighting selected
189 BPM. Equal weighting selects 126 BPM, consistent with WaveTone's displayed result. On the
current envelope, the equal-weight coarse scores are approximately 95.0 at 126 BPM and 53.5 at
189 BPM.
