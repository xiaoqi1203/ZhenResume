import { cn } from '@/lib/utils';

export interface RadarDimension {
  label: string;
  /** 0–100 */
  value: number;
}

export interface ScoreRadarProps {
  /** 顺序任意，建议：ATS / 影响力 / 清晰度 / 竞争力 / 风险 */
  dimensions: RadarDimension[];
  className?: string;
}

const SIZE = 240;
const CENTER = SIZE / 2;
const MAX_RADIUS = 92;
const LEVELS = [0.25, 0.5, 0.75, 1];

function pointAt(index: number, total: number, ratio: number): [number, number] {
  const angle = -Math.PI / 2 + (index * 2 * Math.PI) / total;
  return [
    CENTER + Math.cos(angle) * MAX_RADIUS * ratio,
    CENTER + Math.sin(angle) * MAX_RADIUS * ratio,
  ];
}

function toPolygon(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
}

/**
 * 纯 SVG 自绘五维评分雷达图（零图表依赖）。
 * 附带右侧数值条形，保证小屏可读。
 */
export function ScoreRadar({ dimensions, className }: ScoreRadarProps) {
  const total = dimensions.length;
  if (total < 3) {
    return (
      <p className={cn('text-sm text-ink-500', className)}>评分维度数据不足，无法绘制雷达图。</p>
    );
  }

  const axes = dimensions.map((_, index) => ({
    outer: pointAt(index, total, 1),
    label: pointAt(index, total, 1.24),
  }));

  const dataPoints = dimensions.map((dimension, index) =>
    pointAt(index, total, Math.min(100, Math.max(0, dimension.value)) / 100),
  );

  return (
    <div className={cn('flex flex-col items-center gap-6 sm:flex-row sm:items-center', className)}>
      <svg
        // viewBox 向外留出边距：轴标签画在 1.24 倍半径处，若按图形本身裁切，
        // 左右两侧的标签会被 SVG 边界切掉（曾出现「抗风险 100」显示成「险 00」）。
        viewBox="-44 -26 328 300"
        className="h-60 w-60 shrink-0"
        role="img"
        aria-label="五维评分雷达图"
      >
        {LEVELS.map((level) => (
          <polygon
            key={level}
            points={toPolygon(dimensions.map((_, index) => pointAt(index, total, level)))}
            fill="none"
            className="stroke-ink-200"
            strokeWidth={1}
          />
        ))}

        {axes.map((axis, index) => (
          <line
            key={`axis-${index}`}
            x1={CENTER}
            y1={CENTER}
            x2={axis.outer[0]}
            y2={axis.outer[1]}
            className="stroke-ink-200"
            strokeWidth={1}
          />
        ))}

        <polygon
          points={toPolygon(dataPoints)}
          className="fill-brand-500/20 stroke-brand-500"
          strokeWidth={2}
          strokeLinejoin="round"
        />

        {dataPoints.map(([x, y], index) => (
          <circle key={`dot-${index}`} cx={x} cy={y} r={3.2} className="fill-ink-950" />
        ))}

        {dimensions.map((dimension, index) => {
          const [x, y] = axes[index].label;
          const anchor =
            Math.abs(x - CENTER) < 6 ? 'middle' : x > CENTER ? 'start' : 'end';
          return (
            <g key={`label-${dimension.label}`}>
              <text
                x={x}
                y={y}
                textAnchor={anchor}
                dominantBaseline="middle"
                fontSize={11}
                className="fill-ink-500"
              >
                {dimension.label}
              </text>
              <text
                x={x}
                y={y + 12}
                textAnchor={anchor}
                dominantBaseline="middle"
                fontSize={12}
                fontWeight={600}
                className="fill-ink-900"
              >
                {Math.round(dimension.value)}
              </text>
            </g>
          );
        })}
      </svg>

      <ul className="w-full space-y-2.5">
        {dimensions.map((dimension) => {
          const value = Math.round(dimension.value);
          return (
            <li key={`bar-${dimension.label}`} className="space-y-1">
              <div className="flex items-baseline justify-between text-xs">
                <span className="font-medium text-ink-600">{dimension.label}</span>
                <span className="tnum text-ink-900">{value}</span>
              </div>
              <div className="h-1 w-full overflow-hidden bg-ink-200">
                <div
                  className="h-full bg-ink-950"
                  style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default ScoreRadar;
