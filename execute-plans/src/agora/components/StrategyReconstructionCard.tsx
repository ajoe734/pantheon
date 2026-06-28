import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadServantReconstruction } from "./workshop-card-types";

export interface StrategyReconstructionCardProps {
  card: WorkshopCard;
  payload: PayloadServantReconstruction;
  onContinueDiscussion?: (cardId: string) => void;
}

const STATUS_BORDER: Record<string, string> = {
  informational: "#303846",
  action_required: "#7a5a23",
  running: "#31516f",
  completed: "#2f6c54",
  failed: "#7a3131",
  stale: "#303846",
};

function compact(items: Array<string | undefined | null>, limit: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const value = item?.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
    if (out.length >= limit) break;
  }
  return out;
}

function confidenceLabel(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

function coreText(payload: PayloadServantReconstruction): string {
  const firstDefinition = payload.explicit_definitions?.[0]?.trim();
  if (firstDefinition) return firstDefinition;
  const firstStep = payload.causal_chain[0];
  if (firstStep) return `${firstStep.premise} -> ${firstStep.mechanism}`;
  return payload.strategy_title;
}

function researchQuestions(payload: PayloadServantReconstruction): string[] {
  return compact(
    [
      ...payload.causal_chain.map((step) => step.expected_observation),
      ...(payload.proposed_next_actions ?? []),
    ],
    7,
  );
}

function recognizedComponents(payload: PayloadServantReconstruction): string[] {
  return compact(
    [
      ...(payload.explicit_definitions ?? []).slice(1),
      ...payload.causal_chain.map((step) => step.premise),
      ...(payload.servant_inferences ?? []).map((inf) => inf.statement),
    ],
    12,
  );
}

function limitationItems(payload: PayloadServantReconstruction): string[] {
  return compact(
    [
      ...(payload.uncertainties ?? []),
      "公開資料僅能支持統計關聯與研究限制，不能作身份或違法行為認定。",
    ],
    4,
  );
}

export function StrategyReconstructionCard({
  card,
  payload,
  onContinueDiscussion,
}: StrategyReconstructionCardProps): JSX.Element {
  const border = STATUS_BORDER[card.status] ?? STATUS_BORDER.informational;
  const questions = researchQuestions(payload);
  const components = recognizedComponents(payload);
  const limitations = limitationItems(payload);

  return (
    <article
      data-testid={`workshop-card-servant-${card.card_id}`}
      style={{
        border: `1px solid ${border}`,
        borderRadius: 13,
        background: "#171b22",
        color: "#f3efe7",
        overflow: "hidden",
        boxShadow: "0 18px 44px rgba(0,0,0,0.22)",
      }}
    >
      <div
        data-testid={`strategy-reconstruction-card-${card.card_id}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "13px 16px",
          borderBottom: "1px solid #2a303b",
          background: "#202631",
        }}
      >
        <span aria-hidden="true" style={{ color: "#e8b750", fontWeight: 700 }}>
          *
        </span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>
            策略重構卡 · Strategy Reconstruction
          </div>
          <div style={{ fontSize: 11, color: "#9aa4b2", marginTop: 2 }}>
            {card.title || payload.strategy_title}
          </div>
        </div>
        <span
          style={{
            marginLeft: "auto",
            color: "#737d8e",
            fontFamily: "IBM Plex Mono, monospace",
            fontSize: 11,
          }}
        >
          #{card.sequence_no}
        </span>
      </div>

      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
        {card.summary && (
          <div style={{ color: "#aeb7c4", fontSize: 12, lineHeight: 1.55 }}>{card.summary}</div>
        )}

        <section data-testid={`strategy-reconstruction-card-${card.card_id}-core`}>
          <div style={{ color: "#e8b750", fontSize: 10.5, fontWeight: 700, marginBottom: 7 }}>
            A · 策略核心
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.65 }}>{coreText(payload)}</div>
        </section>

        <section data-testid={`workshop-card-servant-${card.card_id}-chain`}>
          <div style={{ color: "#8d96a5", fontSize: 10.5, fontWeight: 700, marginBottom: 9 }}>
            B · 推導出的研究子問題
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {questions.map((question, index) => (
              <div
                key={`${question}-${index}`}
                style={{ display: "flex", gap: 9, color: "#c4cbd6", fontSize: 12, lineHeight: 1.5 }}
              >
                <span style={{ color: "#e8b750", fontFamily: "IBM Plex Mono, monospace" }}>
                  {index + 1}
                </span>
                <span>{question}</span>
              </div>
            ))}
          </div>
        </section>

        <section data-testid={`workshop-card-servant-${card.card_id}-inferences`}>
          <div style={{ color: "#8d96a5", fontSize: 10.5, fontWeight: 700, marginBottom: 9 }}>
            C · 已辨識策略元件
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {components.map((component) => (
              <span
                key={component}
                style={{
                  color: "#c4cbd6",
                  background: "#202631",
                  border: "1px solid #303846",
                  borderRadius: 7,
                  padding: "4px 10px",
                  fontSize: 11,
                }}
              >
                {component}
              </span>
            ))}
          </div>
          {payload.servant_inferences && payload.servant_inferences.length > 0 && (
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5 }}>
              {payload.servant_inferences.map((inf, index) => (
                <div key={`${inf.statement}-${index}`} style={{ fontSize: 11.5, color: "#d9b76c" }}>
                  僕人推定 {confidenceLabel(inf.confidence)}：{inf.statement}
                  {inf.needs_confirmation ? "（待裁示）" : ""}
                </div>
              ))}
            </div>
          )}
        </section>

        <section
          data-testid={`strategy-reconstruction-card-${card.card_id}-limitations`}
          style={{
            background: "rgba(232,183,80,0.12)",
            border: "1px solid rgba(232,183,80,0.5)",
            borderRadius: 10,
            padding: 12,
          }}
        >
          <div style={{ color: "#e8b750", fontSize: 10.5, fontWeight: 700, marginBottom: 6 }}>
            D · 研究限制
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {limitations.map((item) => (
              <div key={item} style={{ color: "#f3efe7", fontSize: 12, lineHeight: 1.55 }}>
                {item}
              </div>
            ))}
          </div>
        </section>

        {payload.contradictions && payload.contradictions.length > 0 && (
          <section
            data-testid={`workshop-card-servant-${card.card_id}-contradictions`}
            style={{
              color: "#f2a6a6",
              fontSize: 12,
              lineHeight: 1.5,
              borderTop: "1px solid #303846",
              paddingTop: 10,
            }}
          >
            衝突：{payload.contradictions.join("；")}
          </section>
        )}

        {onContinueDiscussion && (
          <div>
            <button
              data-testid={`card-${card.card_id}-discuss`}
              onClick={() => onContinueDiscussion(card.card_id)}
              style={{
                fontSize: 11,
                padding: "5px 11px",
                borderRadius: 7,
                border: "1px solid #303846",
                background: "#202631",
                color: "#c4cbd6",
                cursor: "pointer",
              }}
            >
              交代僕人
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
