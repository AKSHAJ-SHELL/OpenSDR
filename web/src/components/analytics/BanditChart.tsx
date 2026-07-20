"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ArmPosterior } from "@/lib/types";

const COLORS = ["#D6452F", "#1F5F8B", "#1B7A4C", "#A15C07", "#5C6570"];

function betaPdf(alpha: number, beta: number, x: number) {
  // log-space Beta pdf for stability
  const logGamma = (z: number): number => {
    // Lanczos approximation (good enough for viz)
    const g = 7;
    const p = [
      0.99999999999980993, 676.5203681218851, -1259.1392167224028,
      771.32342877765313, -176.61502916214059, 12.507343278686905,
      -0.13857109526572012, 9.984369654078563e-6, 1.5056327351493116e-7,
    ];
    if (z < 0.5) {
      return Math.log(Math.PI / Math.sin(Math.PI * z)) - logGamma(1 - z);
    }
    z -= 1;
    let xAcc = p[0];
    for (let i = 1; i < g + 2; i++) xAcc += p[i] / (z + i);
    const t = z + g + 0.5;
    return (
      0.5 * Math.log(2 * Math.PI) +
      (z + 0.5) * Math.log(t) -
      t +
      Math.log(xAcc)
    );
  };

  if (x <= 0 || x >= 1) return 0;
  const logB = logGamma(alpha) + logGamma(beta) - logGamma(alpha + beta);
  return Math.exp(
    (alpha - 1) * Math.log(x) + (beta - 1) * Math.log(1 - x) - logB,
  );
}

export function BanditChart({ arms, title }: { arms: ArmPosterior[]; title: string }) {
  const data = useMemo(() => {
    const xs = Array.from({ length: 80 }, (_, i) => (i + 1) / 400); // 0..0.2
    return xs.map((x) => {
      const row: Record<string, number> = { x: Number((x * 100).toFixed(2)) };
      arms.forEach((arm) => {
        const key = arm.name || arm.variant_id.slice(0, 8);
        row[key] = betaPdf(arm.alpha, arm.beta, x);
      });
      return row;
    });
  }, [arms]);

  const keys = arms.map((a) => a.name || a.variant_id.slice(0, 8));

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="font-[family-name:var(--font-display)] text-lg text-ink">
          {title}
        </h3>
        <span className="text-xs text-faint">posterior density · reply rate %</span>
      </div>
      <div className="h-[300px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#E4E7EC" strokeDasharray="3 3" />
            <XAxis
              dataKey="x"
              tick={{ fill: "#8B939E", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "#E4E7EC" }}
              unit="%"
            />
            <YAxis
              tick={{ fill: "#8B939E", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={36}
            />
            <Tooltip
              contentStyle={{
                borderRadius: 10,
                border: "1px solid #E4E7EC",
                boxShadow: "var(--shadow)",
              }}
            />
            <Legend />
            {keys.map((key, i) => (
              <Area
                key={key}
                type="monotone"
                dataKey={key}
                stroke={COLORS[i % COLORS.length]}
                fill={COLORS[i % COLORS.length]}
                fillOpacity={0.18}
                strokeWidth={2}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-[11px] uppercase tracking-wide text-faint">
            <tr>
              <th className="pb-2 font-semibold">Variant</th>
              <th className="pb-2 font-semibold">Trials</th>
              <th className="pb-2 font-semibold">Mean</th>
              <th className="pb-2 font-semibold">α / β</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((arm) => (
              <tr key={arm.variant_id} className="border-t border-line">
                <td className="py-2.5 font-medium text-ink">{arm.name}</td>
                <td className="py-2.5 text-muted">{arm.trials}</td>
                <td className="py-2.5 text-muted">
                  {(arm.posterior_mean * 100).toFixed(2)}%
                </td>
                <td className="py-2.5 text-muted">
                  {arm.alpha.toFixed(1)} / {arm.beta.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
