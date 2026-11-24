import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from "@angular/core";

import { NgxEchartsDirective, provideEchartsCore } from "ngx-echarts";
import * as echarts from "echarts/core";
import { GaugeChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import { TitleComponent, TooltipComponent } from "echarts/components";

echarts.use([GaugeChart, CanvasRenderer, TitleComponent, TooltipComponent]);

@Component({
  selector: "app-completeness-gauge",
  standalone: true,
  imports: [NgxEchartsDirective],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div class="gauge-card">
      <div class="gauge-header">
        <h3 class="gauge-title">{{ title() }}</h3>
      </div>
      <div class="chart-wrapper" dir="ltr">
        <div echarts [options]="chartOptions()" class="chart-container"></div>
      </div>
    </div>
  `,
  styles: [
    `
      .gauge-card {
        display: flex;
        flex-direction: column;
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.25rem;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
        height: 100%;
        box-sizing: border-box;
        direction: rtl;
        font-family: var(--font-family-sans, "Vazirmatn", sans-serif);
      }
      .gauge-header {
        margin-bottom: 0.5rem;
      }
      .gauge-title {
        font-size: 1.125rem;
        font-weight: 600;
        color: #1e293b;
        margin: 0;
      }
      .chart-wrapper {
        position: relative;
        height: 280px;
        width: 100%;
        overflow: hidden;
      }
      .chart-container {
        height: 100%;
        width: 100%;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CompletenessGaugeComponent {
  readonly score = input<number>(0);
  readonly title = input<string>("تکمیل متادیتا");

  readonly chartOptions = computed<echarts.EChartsCoreOption>(() => {
    const value = Math.round(this.score() * 100) / 100;
    return {
      tooltip: {
        formatter: "{a} <br/>{b} : {c}%",
      },
      series: [
        {
          name: "Completeness",
          type: "gauge",
          center: ["50%", "52%"],
          radius: "82%",
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 100,
          splitNumber: 5,
          itemStyle: {
            color: "#00897B",
          },
          progress: {
            show: true,
            roundCap: true,
            width: 18,
          },
          pointer: {
            length: "60%",
            width: 6,
          },
          axisLine: {
            lineStyle: {
              width: 18,
              color: [
                [0.4, "#ef4444"],
                [0.75, "#f59e0b"],
                [1.0, "#10b981"],
              ],
            },
          },
          axisTick: {
            distance: -28,
            length: 6,
            lineStyle: {
              color: "#94a3b8",
              width: 1,
            },
          },
          splitLine: {
            distance: -34,
            length: 12,
            lineStyle: {
              color: "#64748b",
              width: 2,
            },
          },
          axisLabel: {
            distance: -20,
            color: "#64748b",
            fontSize: 12,
          },
          title: {
            show: true,
            offsetCenter: [0, "42%"],
            fontSize: 13,
            fontFamily: "inherit",
            color: "#64748b",
          },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, "12%"],
            fontSize: 24,
            fontWeight: 800,
            fontFamily: "inherit",
            formatter: "{value}%",
            color: "#0f172a",
          },
          data: [{ value, name: "نرخ تکمیل" }],
        },
      ],
    };
  });
}
