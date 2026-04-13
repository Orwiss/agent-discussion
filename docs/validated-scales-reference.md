# Validated Psychometric Scales for Independent vs Discussion-Based AI Multi-Agent Ideation Study

> Reference document for supplementary measures to pair with the Creativity Support Index (CSI).
> All scales listed below are validated, published instruments with established psychometric properties.

---

## 1. Perceived Creativity Scales (인지된 창의성)

### 1.1 Creative Product Semantic Scale (CPSS)

| Field | Detail |
|-------|--------|
| **Authors** | Besemer & O'Quin (original 1986); O'Quin & Besemer (revised 1989) |
| **Published** | Creativity Research Journal, 2(4), 267-278 |
| **Items** | 55 items (revised version); 7-point bipolar semantic differential |
| **Dimensions** | 3 main dimensions, 11 sub-factors |
| **Measures** | How creative a PRODUCT/OUTPUT is judged to be (not the person's creativity) |
| **Cronbach's alpha** | .69 to .97 across subscales (Besemer 1998; Besemer & O'Quin 1999; Horn & Salvendy 2006) |

**Three Main Dimensions:**

1. **Novelty** (originality, surprise, germinal quality)
   - Is the product original?
   - Is the product surprising?
   - Is the product germinal (likely to suggest new ideas)?

2. **Resolution** (valuable, useful, logical)
   - Is the product useful?
   - Is the product valuable?
   - Is the product logically sound?

3. **Elaboration & Synthesis** (later renamed "Style"; complex, understandable, well-crafted, organic, elegant)
   - Example bipolar pairs for "Elegant" subscale: graceful-awkward, refined-busy, coarse-elegant, repelling-charming, attractive-unattractive

**Why this fits your study:** Directly measures perceived creativity of AI-generated design outputs. Participants rate the IDEAS produced by each system (independent vs discussion), not their own creativity. This is exactly what you need.

**Key citations in AI/design contexts:**
- Horn, D., & Salvendy, G. (2006). Consumer-based assessment of product creativity. Human Factors and Ergonomics in Manufacturing, 16(2), 155-175.
- Chulvi, V., et al. (2012). Influence of type of idea-generation method on creativity of designs. Research in Engineering Design, 23(1), 1-11.
- Recent LLM-based creativity evaluation papers (2024-2025) use CPSS dimensions as ground truth for validating automated scoring.

**Recommendation:** Use a SHORT FORM (select key items from Novelty and Resolution subscales, ~12-15 items) rather than all 55 items to reduce participant fatigue.

---

### 1.2 Consensual Assessment Technique (CAT)

| Field | Detail |
|-------|--------|
| **Authors** | Amabile, T. M. (1982, 1996) |
| **Published** | Journal of Personality and Social Psychology; "Creativity in Context" (1996 book) |
| **Items** | Expert ratings on multiple dimensions (typically 5-7 dimensions rated per product) |
| **Measures** | Expert consensus on how creative a product is |
| **Inter-rater reliability** | Typically .80-.90 (coefficient alpha across raters) |

**How it works:** Independent expert judges rate products on creativity, technical execution, and aesthetic appeal. No predefined rubric -- relies on expert consensus.

**Typical rating dimensions:**
1. "How creative is this product?" (1-7 scale)
2. "How novel is this product?" (1-7 scale)
3. "How appropriate/useful is this product?" (1-7 scale)
4. "How technically well-executed is this product?" (1-7 scale)
5. "How aesthetically pleasing is this product?" (1-7 scale)

**Why this fits your study:** Gold standard for creativity assessment in design research. However, requires EXPERT raters (not self-report). Best used as a complementary expert evaluation alongside participant self-report measures.

**Limitation for your study:** CAT requires expert judges separate from participants. It measures objective/expert-assessed creativity, not participant-perceived creativity. Use CPSS for participant self-report, CAT for expert validation.

---

## 2. Trust in AI Scales

### 2.1 Trust in Automated Systems Scale (TASS / Jian Scale)

| Field | Detail |
|-------|--------|
| **Authors** | Jian, J. Y., Bisantz, A. M., & Drury, C. G. (2000) |
| **Published** | International Journal of Cognitive Ergonomics, 4(1), 53-71 |
| **Items** | 12 items, 7-point Likert (1 = Not at all, 7 = Extremely) |
| **Measures** | Trust and distrust in automated systems as a single bipolar dimension |
| **Cronbach's alpha** | .94 (mean of 12 items) |

**Complete 12 Items:**

1. The system is deceptive. (distrust)
2. The system behaves in an underhanded manner. (distrust)
3. I am suspicious of the system's intent, action, or outputs. (distrust)
4. I am wary of the system. (distrust)
5. The system's actions will have a harmful or injurious outcome. (distrust)
6. I am confident in the system. (trust)
7. The system provides security. (trust)
8. The system has integrity. (trust)
9. The system is dependable. (trust)
10. The system is reliable. (trust)
11. I can trust the system. (trust)
12. I am familiar with the system. (trust)

**Short Form (S-TIAS, 3 items, alpha = .97):**
1. I am confident in the AI assistant.
2. The AI assistant is reliable.
3. I can trust the AI assistant.

**Why this fits your study:** Most widely used trust scale in automation/AI research. Can compare whether discussion-based multi-agent systems generate more/less trust than independent agents. The short form (3 items) is practical if you have many other scales.

**Key citations in AI contexts (2024-2025):**
- Gutzwiller, R. S., et al. (2019). Positive bias in the 'Trust in Automated Systems Survey'. Proceedings of HFES.
- Frontiers in AI (2025). Measuring trust in artificial intelligence: validation of an established scale and its short form.

**Known limitation:** Gutzwiller et al. (2019) identified a positive bias in the scale -- items 6-12 (trust items) tend to score higher than items 1-5 (distrust items) are scored low, potentially inflating overall trust scores.

---

### 2.2 Human-Computer Trust Scale (HCT)

| Field | Detail |
|-------|--------|
| **Authors** | Madsen, M. & Gregor, S. (2000) |
| **Published** | Proceedings of the 11th Australasian Conference on Information Systems |
| **Items** | 25 items, 5 items per subscale |
| **Measures** | Trust in AI/computer decision aids across 5 facets |
| **Cronbach's alpha** | .94 (overall) |

**Five Subscales:**

1. **Perceived Reliability** (5 items) - Consistency and dependability of the system
2. **Perceived Technical Competence** (5 items) - System's ability to perform tasks correctly
3. **Perceived Understandability** (5 items) - How well the user understands system behavior
4. **Faith** (5 items) - Belief that the system will work well in untested situations
5. **Personal Attachment** (5 items) - Emotional bond with the system

**Two Higher-Order Dimensions:**
- Cognition-Based Trust (CBT): Understandability + Technical Competence
- Affect-Based Trust (ABT): Faith + Personal Attachment + Reliability

**Why this fits your study:** More nuanced than the Jian scale -- distinguishes cognitive from affective trust. Particularly useful for your study because "Personal Attachment" and "Faith" could differ meaningfully between independent vs. discussion-based AI systems.

---

### 2.3 Trust Perception Scale-HRI (TPS)

| Field | Detail |
|-------|--------|
| **Authors** | Schaefer, K. E. (2013, 2016) |
| **Published** | Springer: Foundations of Trusted Autonomy, 10, 137-154 |
| **Items** | 40 items (full); 14 items (short form); percentage-based scoring |
| **Measures** | Trust perceptions specifically in human-robot/agent interaction |
| **Validation** | 6 experiments in scale development from 156-item pool |

**Why this fits your study:** Designed specifically for interaction with autonomous agents -- closer to your multi-agent AI context than general automation trust scales.

---

## 3. AI Collaboration / Co-Creation Experience Scales

### 3.1 Sense of Agency Scale (SoAS)

| Field | Detail |
|-------|--------|
| **Authors** | Tapal, A., Oren, E., Dar, R., & Eitam, B. (2017) |
| **Published** | Frontiers in Psychology, 8, 1552 |
| **Items** | 13 items (from initial 36), 7-point Likert |
| **Measures** | Consciously perceived control over one's mind, body, and immediate environment |
| **Validation** | Exploratory and confirmatory factor analysis; correlates with OC symptoms (r = .35) |

**Two Subscales:**
1. **Sense of Positive Agency (SoPA)** - Feeling of being in control
2. **Sense of Negative Agency (SoNA)** - Feeling of lacking control

**Sample Items:**
- "I am in full control of what I do."
- "My movements are automatic -- my body simply makes them." (reverse)
- "While I am in action, I feel like I am a remote-controlled robot." (reverse)
- "The outcomes of my actions generally surprise me." (reverse)

**Why this fits your study:** Measures whether participants feel they are the "author" of the creative output. Critical comparison: do designers feel more agency with independent AI (they choose from separate outputs) vs. discussion-based AI (agents negotiate and present consensus)?

**Adaptation needed:** The original SoAS is general/context-free. For your study, adapt items to the specific AI interaction context, e.g., "I felt in full control of the design process while using this system."

---

### 3.2 CSI Collaboration Subscale (Built into your primary measure)

The CSI already contains a Collaboration factor. This captures some co-creation experience, but is limited to 2 items. You likely need supplementary measures.

**CSI Collaboration Items:**
- "I was able to be creative when using this tool."
- "The tool allowed me to be creative in partnership with it."

---

### 3.3 Ad-hoc Co-Creation Scales from Recent CHI/DIS Papers

While no single validated "Human-AI Co-Creation Scale" exists yet as a standalone published instrument, recent papers (2024-2025) have converged on measuring these dimensions with adapted items:

**Commonly measured constructs (adapt from existing validated instruments):**

| Construct | Typical Items | Source Scales to Adapt From |
|-----------|--------------|---------------------------|
| Perceived Control | "I felt in control of the creative process" | SoAS (Tapal 2017) |
| Ownership of Output | "I feel the final output is mine" | Psychological Ownership Scale (Pierce et al. 2004) |
| AI Contribution | "The AI contributed meaningfully to the result" | Custom, frequently used in CHI 2024-2025 |
| Collaboration Quality | "Working with the AI felt like a natural collaboration" | Collaborative Work Scale items |
| Creative Partnership | "The AI understood my creative intent" | Custom, emerging in HCI |

---

## 4. Output Quality Perception Scales

### 4.1 CPSS Short Form for Output Quality (see Section 1.1)

The most validated approach. Select items from the three CPSS dimensions:

**Recommended subset for your study (12 items):**

**Novelty (4 items):**
- Usual --- Unusual
- Predictable --- Novel
- Unique --- Ordinary (reverse)
- Surprising --- Commonplace (reverse)

**Usefulness/Resolution (4 items):**
- Useful --- Useless (reverse)
- Relevant --- Irrelevant (reverse)
- Valuable --- Worthless (reverse)
- Practical --- Impractical (reverse)

**Elaboration/Quality (4 items):**
- Well-crafted --- Crude (reverse)
- Elegant --- Awkward (reverse)
- Refined --- Rough (reverse)
- Attractive --- Unattractive (reverse)

### 4.2 Single-Item and Short Measures for Quick Assessment

For per-idea ratings (when participants rate many individual outputs), use single items:

| Dimension | Question | Scale |
|-----------|----------|-------|
| Overall Creativity | "How creative is this idea?" | 1-7 Likert |
| Novelty | "How novel/original is this idea?" | 1-7 Likert |
| Usefulness | "How useful/practical is this idea?" | 1-7 Likert |
| Surprise | "How surprising/unexpected is this idea?" | 1-7 Likert |
| Relevance | "How relevant is this idea to the design brief?" | 1-7 Likert |
| Feasibility | "How feasible is this idea to implement?" | 1-7 Likert |

These dimensions are derived from the widely-cited framework by Shah, Vargas-Hernandez, & Smith (2003) for design ideation metrics (novelty, variety, quality, quantity).

---

## 5. Supplementary Measures Commonly Paired with CSI

### 5.1 NASA-TLX (NASA Task Load Index)

| Field | Detail |
|-------|--------|
| **Authors** | Hart, S. G. & Staveland, L. E. (1988) |
| **Published** | Advances in Psychology, 52, 139-183 |
| **Items** | 6 subscales, 21-point scale each (0-100 in 5-point steps) |
| **Measures** | Subjective workload during task performance |
| **Reliability** | Test-retest r = .83 |

**Six Subscales:**

1. **Mental Demand**: "How mentally demanding was the task? (Easy/Demanding)"
2. **Physical Demand**: "How physically demanding was the task? (Low/High)"
3. **Temporal Demand**: "How much time pressure did you feel? (Low/High)"
4. **Performance**: "How successful were you in performing the task? (Perfect/Failure)"
5. **Effort**: "How hard did you have to work to accomplish your level of performance? (Low/High)"
6. **Frustration**: "How irritated, stressed, and annoyed vs. content, relaxed, and complacent did you feel? (Low/High)"

**Why pair with CSI:** CSI measures HOW WELL the tool supports creativity; NASA-TLX measures HOW MUCH WORK it takes. A tool could score high on CSI (great creativity support) but also high on NASA-TLX (exhausting to use). For your study: does discussion-based multi-agent AI reduce or increase cognitive workload compared to independent AI?

**Recommendation for your study:** Use Raw TLX (no weighting procedure) -- simpler and equally valid (Hart, 2006). Drop Physical Demand (irrelevant for software interaction) = 5 items.

---

### 5.2 System Usability Scale (SUS)

| Field | Detail |
|-------|--------|
| **Authors** | Brooke, J. (1996) |
| **Published** | Usability Evaluation in Industry, Taylor & Francis, pp. 189-194 |
| **Items** | 10 items, 5-point Likert (Strongly Disagree to Strongly Agree) |
| **Measures** | Overall perceived usability of a system |
| **Cronbach's alpha** | .90+ (Bangor et al. 2008; Lewis & Sauro 2009) |

**All 10 Items:**

1. I think that I would like to use this system frequently.
2. I found the system unnecessarily complex. (R)
3. I thought the system was easy to use.
4. I think that I would need the support of a technical person to be able to use this system. (R)
5. I found the various functions in this system were well integrated.
6. I thought there was too much inconsistency in this system. (R)
7. I would imagine that most people would learn to use this system very quickly.
8. I found the system very cumbersome to use. (R)
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going with this system. (R)

**Two factors:** Usability (8 items, alpha = .91) and Learnability (Items 4 & 10, alpha = .70)

**Scoring:** Each item 0-4. Sum x 2.5 = score out of 100. Score > 68 = above average.

**Why pair with CSI:** Controls for basic usability differences between your two conditions. If the discussion-based system is harder to USE (not less creative), SUS will capture that separately from CSI.

---

### 5.3 User Experience Questionnaire (UEQ)

| Field | Detail |
|-------|--------|
| **Authors** | Laugwitz, B., Held, T., & Schrepp, M. (2008) |
| **Published** | HCI and Usability for Education and Work (USAB 2008), LNCS 5298, pp. 63-76 |
| **Items** | 26 items (full); 8 items (UEQ-S short version); 7-point semantic differential |
| **Measures** | User experience across pragmatic and hedonic qualities |
| **Validation** | Available in 30+ languages; extensive benchmark data |

**Six Scales:**

1. **Attractiveness** (6 items): Overall impression (annoying/enjoyable, good/bad, etc.)
2. **Perspicuity** (4 items): Ease of learning (not understandable/understandable, easy/difficult)
3. **Efficiency** (4 items): Task completion without unnecessary effort (fast/slow, efficient/inefficient)
4. **Dependability** (4 items): User control (unpredictable/predictable, secure/not secure)
5. **Stimulation** (4 items): Excitement and motivation (valuable/inferior, boring/exciting)
6. **Novelty** (4 items): Innovation and creativity (creative/dull, innovative/conservative)

**Why pair with CSI:** UEQ captures hedonic qualities (Stimulation, Novelty) that overlap with creativity support but also pragmatic qualities (Efficiency, Perspicuity) that CSI does not measure. The UEQ-S (8 items) is recommended if you already have many scales.

---

### 5.4 AttrakDiff

| Field | Detail |
|-------|--------|
| **Authors** | Hassenzahl, M., Burmester, M., & Koller, F. (2003) |
| **Published** | Mensch & Computer 2003, pp. 187-196 |
| **Items** | 28 items, 7-point semantic differential |
| **Measures** | Pragmatic quality, hedonic quality (stimulation + identification), attractiveness |

**Four Dimensions:**
1. **Pragmatic Quality (PQ)** - Usability, task-oriented
2. **Hedonic Quality - Stimulation (HQ-S)** - Novel, interesting, stimulating
3. **Hedonic Quality - Identity (HQ-I)** - Impression management, identification
4. **Attractiveness (ATT)** - Overall appeal

**Why pair with CSI:** Hedonic Quality-Stimulation captures aspects close to creativity support; Pragmatic Quality captures usability. Especially useful if you want to differentiate whether a system is perceived as more "stimulating" vs. merely "usable."

---

## Summary: Recommended Scale Battery for Your Study

Given your study design (Independent AI vs. Discussion-Based AI for design ideation, within-subjects or between-subjects), here is a recommended combination:

### Tier 1: Primary Measures (MUST include)

| Scale | Items | Measures | Time |
|-------|-------|----------|------|
| **CSI** (Creativity Support Index) | 12 items + 15 paired comparisons | Creativity support quality | ~5 min |
| **CPSS Short Form** (or custom items from CPSS) | 12 items | Perceived creativity of outputs | ~3 min |
| **Jian Trust Scale** (short form) | 3-12 items | Trust in AI system | ~2 min |

### Tier 2: Important Supplements

| Scale | Items | Measures | Time |
|-------|-------|----------|------|
| **NASA-TLX** (Raw, minus Physical Demand) | 5 items | Cognitive workload | ~2 min |
| **Sense of Agency** (adapted SoAS items) | 5-7 items | Perceived control/authorship | ~2 min |
| **Output Quality Ratings** (single items per idea) | 4-6 items per idea | Novelty, usefulness, surprise, relevance | ~1 min/idea |

### Tier 3: Optional (choose based on research questions)

| Scale | Items | Measures | Time |
|-------|-------|----------|------|
| **SUS** | 10 items | System usability | ~3 min |
| **UEQ-S** | 8 items | User experience | ~2 min |
| **HCT** (Madsen & Gregor) | 25 items | Multi-faceted trust | ~5 min |

### Estimated Total Survey Time

- Tier 1 only: ~10 minutes per condition
- Tier 1 + Tier 2: ~17 minutes per condition
- Tier 1 + Tier 2 + Tier 3 (pick 1): ~20-22 minutes per condition

---

## Common Scale Combinations in CHI/DIS Creativity Support Tool Papers (2024-2025)

Based on literature review, these are the most frequently observed combinations:

1. **CSI + NASA-TLX** -- Most common pairing. Creativity support + workload.
2. **CSI + SUS** -- Creativity support + usability baseline.
3. **CSI + NASA-TLX + custom creativity ratings** -- Full evaluation: creativity support + workload + output assessment.
4. **CSI + UEQ + trust items** -- Emerging pattern in AI tool papers.
5. **CSI + CAT (expert evaluation)** -- Creativity support (participant view) + creativity assessment (expert view).

---

## Key References

### Perceived Creativity
- Besemer, S. P., & O'Quin, K. (1986). Analyzing creative products: Refinement and test of a judging instrument. Journal of Creative Behavior, 20(2), 115-126.
- O'Quin, K., & Besemer, S. P. (1989). The development, reliability, and validity of the revised creative product semantic scale. Creativity Research Journal, 2(4), 267-278.
- Amabile, T. M. (1982). Social psychology of creativity: A consensual assessment technique. Journal of Personality and Social Psychology, 43(5), 997-1013.

### Trust in AI
- Jian, J. Y., Bisantz, A. M., & Drury, C. G. (2000). Foundations for an empirically determined scale of trust in automated systems. International Journal of Cognitive Ergonomics, 4(1), 53-71.
- Madsen, M., & Gregor, S. (2000). Measuring human-computer trust. Proceedings of the 11th Australasian Conference on Information Systems, 53, 6-8.
- Schaefer, K. E. (2016). Measuring trust in human robot interactions. In Foundations of Trusted Autonomy (pp. 137-154). Springer.

### Agency and Co-Creation
- Tapal, A., Oren, E., Dar, R., & Eitam, B. (2017). The Sense of Agency Scale: A measure of consciously perceived control over one's mind, body, and the immediate environment. Frontiers in Psychology, 8, 1552.

### Workload and Usability
- Hart, S. G., & Staveland, L. E. (1988). Development of NASA-TLX. Advances in Psychology, 52, 139-183.
- Hart, S. G. (2006). NASA-Task Load Index (NASA-TLX); 20 years later. Proceedings of HFES, 50(9), 904-908.
- Brooke, J. (1996). SUS: A quick and dirty usability scale. Usability Evaluation in Industry, 189-194.

### User Experience
- Laugwitz, B., Held, T., & Schrepp, M. (2008). Construction and evaluation of a user experience questionnaire. HCI and Usability for Education and Work, LNCS 5298, 63-76.
- Hassenzahl, M., Burmester, M., & Koller, F. (2003). AttrakDiff: A questionnaire to measure perceived hedonic and pragmatic quality. Mensch & Computer 2003, 187-196.

### Creativity Support Index
- Carroll, E. A., & Latulipe, C. (2009). The creativity support index. CHI '09 Extended Abstracts on Human Factors in Computing Systems, 4009-4014.
- Cherry, E., & Latulipe, C. (2014). Quantifying the creativity support of digital tools through the Creativity Support Index. ACM Transactions on Computer-Human Interaction, 21(4), 1-25.
