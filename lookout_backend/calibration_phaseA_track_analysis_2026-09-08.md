=== calibration_null_2026-09-07.csv  (fps=20.0) ===

-- Bottle (required dwell ~8s [Bottle: unscaled/held-posture lower bound]) --
  discarded as scene (no person track): 1758 rows
  distinct tracks: 222
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=8s: 1
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    8410       932   112.6     35.0      0.528        0.549     0.871
  survivors' mean-confidence range: 0.528-0.528  (median-confidence range: 0.549-0.549)

-- Cigarette (required dwell ~3s) --
  discarded as scene (no person track): 360 rows
  distinct tracks: 60
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 2
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    3         1371   289.0     44.7      0.499        0.508     0.828
    8410       492   112.0      3.7      0.438        0.433     0.745
  survivors' mean-confidence range: 0.438-0.499  (median-confidence range: 0.433-0.508)

-- knife (required dwell ~3s) --
  discarded as scene (no person track): 93 rows
  distinct tracks: 37
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 2
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    1         1581   285.9     20.3      0.462        0.436     0.884
    3          651   295.4      3.4      0.361        0.324     0.888
  survivors' mean-confidence range: 0.361-0.462  (median-confidence range: 0.324-0.436)

=== calibration_positive_cigarette_2026-09-07.csv  (fps=10.01) ===

-- Bottle (required dwell ~8s [Bottle: unscaled/held-posture lower bound]) --
  discarded as scene (no person track): 487 rows
  distinct tracks: 16
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=8s: 0

-- Cigarette (required dwell ~3s) --
  discarded as scene (no person track): 129 rows
  distinct tracks: 18
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 1
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    780         63    25.0      4.2      0.610        0.690     0.822
  survivors' mean-confidence range: 0.610-0.610  (median-confidence range: 0.690-0.690)

-- knife (required dwell ~3s) --
  discarded as scene (no person track): 138 rows
  distinct tracks: 16
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 2
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    221        134    28.7     12.1      0.475        0.466     0.753
    780        119    26.8      3.7      0.415        0.399     0.723
  survivors' mean-confidence range: 0.415-0.475  (median-confidence range: 0.399-0.466)

=== calibration_positive_bottle_2026-09-07.csv  (fps=10.0) ===

-- Bottle (required dwell ~8s [Bottle: unscaled/held-posture lower bound]) --
  discarded as scene (no person track): 947 rows
  distinct tracks: 111
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=8s: 4
    track     hits  span_s  dwell_s  mean_conf  median_conf  max_conf
    4584       218    10.0      8.3      0.642        0.695     0.874
    3865       447    19.9     15.9      0.583        0.603     0.861
    5083       341    15.5     14.2      0.557        0.569     0.819
    1944       305    14.1     11.1      0.554        0.557     0.832
  survivors' mean-confidence range: 0.554-0.642  (median-confidence range: 0.557-0.695)

-- Cigarette (required dwell ~3s) --
  discarded as scene (no person track): 573 rows
  distinct tracks: 47
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 0

-- knife (required dwell ~3s) --
  discarded as scene (no person track): 52 rows
  distinct tracks: 26
  tracks surviving vote (hits>=2, hits>=0.4*votes) + dwell>=3s: 0
