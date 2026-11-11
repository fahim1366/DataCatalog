import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { GaugeChart } from 'echarts/charts';
import { CanvasRenderer } from 'echarts/renderers';
import { TitleComponent, TooltipComponent } from 'echarts/components';

echarts.use([GaugeChart, CanvasRenderer, TitleComponent, TooltipComponent]);

@Component({
  selector: 'app-completeness-gauge',
  standalone: true,
  imports: [CommonModule, NgxEchartsDirective],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div class="gauge-card">
      <div class="gauge-header">
        <h3 class="gauge-title">{{ title() }}</h3>
      </div>
      <div echarts [options]="chartOptions()" class="chart-container"></div>
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
      .chart-container {
        height: 320px;
        width: 100%;
        min-height: 280px;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CompletenessGaugeComponent {
  readonly score = input<number>(0);
  readonly title = input<string>('Metadata Completeness');

  readonly chartOptions = computed<echarts.EChartsCoreOption>(() => {
    const value = Math.round(this.score() * 100) / 100;
    return {
      tooltip: {
        formatter: '{a} <br/>{b} : {c}%',
      },
      series: [
        {
          name: 'Completeness',
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 100,
          splitNumber: 5,
          itemStyle: {
            color: '#00897B',
          },
          progress: {
            show: true,
            roundCap: true,
            width: 18,
          },
          pointer: {
            length: '60%',
            width: 6,
          },
          axisLine: {
            lineStyle: {
              width: 18,
              color: [
                [0.4, '#ef4444'],
                [0.75, '#f59e0b'],
                [1.0, '#10b981'],
              ],
            },
          },
          axisTick: {
            distance: -28,
            length: 6,
            lineStyle: {
              color: '#94a3b8',
              width: 1,
            },
          },
          splitLine: {
            distance: -34,
            length: 12,
            lineStyle: {
              color: '#64748b',
              width: 2,
            },
          },
          axisLabel: {
            distance: -20,
            color: '#64748b',
            fontSize: 12,
          },
          detail: {
            valueAnimation: true,
            formatter: '{value}%',
            offsetCenter: [0, '20%'],
            fontSize: 26,
            fontWeight: 'bold',
            color: '#0f172a',
          },
          data: [{ value, name: 'Completeness' }],
        },
      ],
    };
  });
}

