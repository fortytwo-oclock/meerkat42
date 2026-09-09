"use client";

/** Dependency-free inline sparkline for the fleet matrix. */
export function Sparkline({
  values,
  width = 96,
  height = 28,
  color = "currentColor",
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  if (values.length < 2) {
    return <svg width={width} height={height} className="text-muted-foreground/40" />;
  }
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const d = values
    .map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${height - ((v - min) / span) * height}`)
    .join(" ");
  return (
    <svg width={width} height={height} style={{ color }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}
