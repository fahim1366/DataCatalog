import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { SunburstChart } from 'echarts/charts';
import { CanvasRenderer } from 'echarts/renderers';
import { LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import { ClassificationReport } from './models/report.models';

echarts.use([SunburstChart, CanvasRenderer, TitleComponent, TooltipComponent, LegendComponent]);

@Component({
  selector: 'app-classification-sunburst',
  standalone: true,
  imports: [CommonModule, NgxEchartsDirective],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div class="sunburst-card">
      <div class="sunburst-header">
        <h3 class="sunburst-title">{{ title() }}</h3>
        <span class="total-badge" *ngIf="report() as r">
          {{ r.total_classified_columns.toLocaleString() }} Classified Columns
        </span>
      </div>
      <div echarts [options]="chartOptions()" class="chart-container"></div>
    </div>
  `,
  styles: [
    `
      .sunburst-card {
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
      .sunburst-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 0.5rem;
      }
      .sunburst-title {
        font-size: 1.125rem;
        font-weight: 600;
        color: #1e293b;
        margin: 0;
      }
      .total-badge {
        font-size: 0.75rem;
        font-weight: 600;
        color: #0369a1;
        background-color: #e0f2fe;
        padding: 0.25rem 0.625rem;
        border-radius: 9999px;
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
export class ClassificationSunburstComponent {
  readonly report = input<ClassificationReport | null>(null);
  readonly title = input<string>('Classification & PII Distribution');

  readonly chartOptions = computed<echarts.EChartsCoreOption>(() => {
    const rep = this.report();
    const items = rep?.breakdown ?? [];

    const colorMap: Record<string, string> = {
      PUBLIC: '#10b981',
      INTERNAL: '#3b82f6',
      CONFIDENTIAL: '#f59e0b',
      RESTRICTED: '#ef4444',
    };

    const sunburstData = items.map((item) => ({
      name: item.classification_level,
      value: item.column_count,
      itemStyle: {
        color: colorMap[item.classification_level.toUpperCase()] ?? '#8b5cf6',
      },
      children: [
        {
          name: `${item.affected_table_count} Tables`,
          value: item.column_count,
          itemStyle: {
            color: colorMap[item.classification_level.toUpperCase()] ?? '#8b5cf6',
            opacity: 0.75,
          },
        },
      ],
    }));

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const val = params.value ? Number(params.value).toLocaleString() : '0';
          return `<strong>${params.name}</strong><br/>Columns: ${val}`;
        },
      },
      series: [
        {
          type: 'sunburst',
          data: sunburstData,
          radius: [0, '90%'],
          sort: undefined,
          emphasis: {
            focus: 'ancestor',
          },
          levels: [
            {},
            {
              r0: '15%',
              r: '55%',
              itemStyle: {
                borderWidth: 2,
                borderColor: '#ffffff',
              },
              label: {
                rotate: 'tangential',
                fontSize: 12,
                fontWeight: 600,
                color: '#ffffff',
              },
            },
            {
              r0: '55%',
              r: '88%',
              label: {
                align: 'right',
                fontSize: 11,
                color: '#1e293b',
              },
              itemStyle: {
                borderWidth: 1,
                borderColor: '#ffffff',
              },
            },
          ],
        },
      ],
    };
  });
}

