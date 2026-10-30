"use client";

import type { StudentProfile } from "@/lib/types";
import { Tag } from "./ui";

export const LEVEL_LABEL: Record<string, string> = {
  beginner: "入门",
  intermediate: "进阶",
  advanced: "熟练",
};

/**
 * 学生画像视图。
 *
 * 数据来自 `GET /students/{id}/profile?classroom_id=`（带参返回单对象）。
 * 画像是每轮对话自动沉淀的，所以新学生这里会是空的 —— 聊天室那边
 * 用 404 判断并给出「聊几轮就有了」的提示。
 */
export function ProfileView({ profile }: { profile: StudentProfile }) {
  const mastery = Object.entries(profile.knowledge_mastery ?? {});

  return (
    <div className="space-y-3.5">
      <div className="flex items-center gap-2">
        <Tag tone="brand">
          {LEVEL_LABEL[profile.overall_level] ?? profile.overall_level}
        </Tag>
        <span className="text-[11.5px] text-hint">
          掌握度均值 {profile.mastery_average.toFixed(2)}
        </span>
      </div>

      {profile.learning_preference && (
        <div>
          <p className="text-[11.5px] font-medium text-sub">学习偏好</p>
          <p className="mt-1 text-[12px] leading-relaxed text-ink">
            {profile.learning_preference}
          </p>
        </div>
      )}

      <div>
        <p className="text-[11.5px] font-medium text-sub">
          知识点掌握度（{profile.topic_count} 个）
        </p>
        <div className="mt-1.5 space-y-2">
          {mastery.length === 0 ? (
            <p className="text-[12px] text-hint">还没有记录</p>
          ) : (
            mastery.map(([topic, score]) => (
              <div key={topic}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[12px] text-ink">{topic}</span>
                  <span className="shrink-0 text-[11px] text-hint">
                    {score.toFixed(2)}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-soft">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(2, Math.min(100, score * 100))}%`,
                      background:
                        score >= 0.7
                          ? "#12a06b"
                          : score >= 0.4
                            ? "#d99a1f"
                            : "#d0453b",
                    }}
                  />
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div>
        <p className="text-[11.5px] font-medium text-sub">薄弱点</p>
        {profile.weak_points?.length ? (
          <ul className="mt-1.5 space-y-1.5">
            {profile.weak_points.map((point, index) => (
              <li
                key={`${point}-${index}`}
                className="rounded-md bg-danger-soft px-2 py-1.5 text-[11.5px] leading-relaxed text-danger"
              >
                {point}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[12px] text-hint">暂时没发现明显薄弱点</p>
        )}
      </div>

      {profile.updated_at && (
        <p className="text-[11px] text-hint">更新于 {profile.updated_at}</p>
      )}
    </div>
  );
}
