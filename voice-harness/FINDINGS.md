# Molly 음성 파이프라인 — API 비교 측정 결과 (2026-06)

> 같은 입력(golden set)으로 STT·LLM·TTS API를 갈아끼우며 **지연·품질·비용**을 측정해
> 우리 서비스(한국 성인 B1 영어회화 친구 앱)에 맞는 조합을 선정.

## ✅ 권장 구성
| 단계 | 선정 | 근거 |
|---|---|---|
| **STT** | **Deepgram Nova-3** | WER 최저(3.1%), 꼬리지연 ~175ms |
| **LLM** | **Groq Llama-3.3-70b** | 첫문장 ~404ms(최속), 한국어 이해 양호, 영어 출력 안정 |
| **TTS** | **ElevenLabs Flash v2.5** (Jessica) | 첫소리 ~280ms (OpenAI 대비 ~3배 빠름) |

→ **체감 지연 ~0.93초 (p99 1.06초), 성공률 100%, ~$0.0025/턴.**
초기 OpenAI 조합(~3.5초) 대비 **3.7배 단축**, gpt-realtime s2s(~0.58초)에 근접.

## 측정 방법
- golden set 2개(test1·test2), 조합당 6회. 지표는 **p50/p90/p99**.
- 체감 지연 = 발화종료 → 첫 응답음성 = `STT 꼬리 + LLM 첫문장 + TTS 첫소리`.
- 모든 STT는 스트리밍(WebSocket). LLM은 moly-server 경유, 토큰 usage로 실측 비용.

## STT 비교
| 모델 | 꼬리지연 | WER | 비고 |
|---|---|---|---|
| **Deepgram Nova-3** | ~175ms | **3.1%** | 선정 |
| OpenAI realtime | ~190ms | 3.8% | |
| AssemblyAI v3 | ~높음 | 5.8% | |

## LLM 비교 (STT=Deepgram, TTS=ElevenLabs 고정)
| 모델 | 체감 E2E p50 | LLM 첫문장 | tok/s | 비용/1M | 한국어 |
|---|---|---|---|---|---|
| **Groq Llama-3.3-70b** | **932ms** | 404ms | 229 | 저렴 | 이해 양호 · 영어출력 안정 |
| Haiku 4.5 | 1312ms | 797ms | 69 | $1/$5 | 강함 (단 "영어전용" 과해석해 거부) |
| Gemini 2.5 Flash-Lite | 1355ms | 867ms | 160 | **$0.10/$0.40** | 영어 출력 준수 약함(1/3) |
| Gemma 4 26B A4B (DeepInfra)* | — | ~900ms | **28-41** | $0.07/$0.34 | 원래 기획·생성 느림 |

\* Gemma 4는 LLM 직접 측정값(파이프라인 미통합). Google AI Studio 경유는 thinking 모드로 8~18초.

## TTS 비교
| 모델 | 첫소리 | 비고 |
|---|---|---|
| **ElevenLabs Flash v2.5** | ~280ms | 선정, 품질 좋음 |
| OpenAI gpt-4o-mini-tts | ~800ms+ | 느림 |

## 핵심 발견
1. **Groq가 LLM 병목을 해소** — 첫문장 797ms(Haiku) → 404ms. 이제 STT·LLM·TTS가 균형.
2. **페르소나 "Respond in English only"가 역효과** — 모델이 "한국어 못한다"며 거부(캐릭터 붕괴).
   → "한국어는 이해하되 항상 영어로 답, 못 알아듣는다는 말 금지"로 분리하니 Groq 70b 한국어 입력 3/3 영어 응답.
3. **Llama 70b 한국어 이해가 의외로 좋음** — 오픈모델 한국어 약점 우려 해소(코드스위칭 청신호).
4. **원래 기획 Gemma 4(DeepInfra)는 저지연 1위 아님** — 생성 28-41 tok/s로 가장 느림.
5. **s2s(gpt-realtime)** — 1턴은 파이프라인과 비슷하나, 대화가 길어지면 오디오 히스토리 재청구로 **5~11배 비쌈**.

## 비용 (턴당, 근사)
| 구성 | 비용 | 지배 요소 |
|---|---|---|
| 파이프라인 (Groq+ElevenLabs) | ~$0.0025 | TTS(ElevenLabs) |
| s2s (gpt-realtime) | ~$0.0073 | 오디오 출력 + 히스토리 |

LLM 자체 비용: Groq/Gemini-Lite/Gemma 모두 매우 저렴($0.0001 안팎), Haiku ~$0.0005.

## 남은 과제
- **코드스위칭 golden set 확충** → 한국어 섞인 발화로 STT·LLM 재검증
- STT/TTS 실단가 정확화 (Deepgram 분단위, ElevenLabs 크레딧)
- barge-in / 턴테이킹 (제품화 단계 — ElevenLabs Speech Engine 재검토 가능)
