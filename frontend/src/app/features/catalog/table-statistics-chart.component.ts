import {
  ChangeDetectionStrategy,
  Component,
  Input,
  OnChanges,
  SimpleChanges,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { CanvasRenderer } from 'echarts/renderers';
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import {
  TableStatisticPoint,
  TableStatsService,
} from '../../core/services/table-stats.service';

echarts.use([
  LineChart,
  CanvasRenderer,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
]);

@Component({
  selector: 'app-table-statistics-chart',
  standalone: true,
  imports: [CommonModule, NgxEchartsDirective],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div class="stats-chart-card" dir="rtl">
      <div class="chart-header">
        <div class="chart-title-group">
          <span class="chart-icon">📈</span>
          <h3 class="chart-title">روند رشد و حجم فیزیکی داده (Data Growth & Volume)</h3>
        </div>
        @if (dataPoints().length > 0) {
          <span class="points-badge mono-font">
            {{ dataPoints().length }} نقطه زمانی ثبت‌شده
          </span>
        }
      </div>

      @if (isLoading()) {
        <div class="loading-state">
          <div class="spinner"></div>
          <span>در حال دریافت تاریخچه آمار فیزیکی جدول...</span>
        </div>
      } @else if (errorMessage()) {
        <div class="error-state">
          <span>⚠️ {{ errorMessage() }}</span>
        </div>
      } @else if (dataPoints().length === 0) {
        <div class="empty-state">
          <span>هنوز اطلاعات آماری برای این جدول ثبت نشده است.</span>
        </div>
      } @else {
        <div echarts [options]="chartOptions()" class="chart-container"></div>
      }
    </div>
  `,
  styles: [
    `
      .stats-chart-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .chart-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
      }
      .chart-title-group {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .chart-icon {
        font-size: 1.25rem;
      }
      .chart-title {
        margin: 0;
        font-size: 1rem;
        font-weight: 700;
        color: #0f172a;
      }
      .points-badge {
        font-size: 0.75rem;
        background: #f1f5f9;
        color: #475569;
        padding: 2px 8px;
        border-radius: 4px;
      }
      .chart-container {
        height: 320px;
        width: 100%;
      }
      .loading-state,
      .empty-state,
      .error-state {
        height: 200px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        color: #64748b;
        font-size: 0.88rem;
      }
      .error-state {
        color: #dc2626;
      }
      .spinner {
        width: 20px;
        height: 20px;
        border: 3px solid #e2e8f0;
        border-top-color: #2563eb;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }
      @keyframes spin {
        to { transform: rotate(360deg); }
      }
      .mono-font {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TableStatisticsChartComponent implements OnChanges {
  private readonly statsService = inject(TableStatsService);

  @Input() tableId: string | null = null;

  readonly isLoading = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly dataPoints = signal<TableStatisticPoint[]>([]);

  private readonly persianDateFormatter = new Intl.DateTimeFormat('fa-IR', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['tableId'] && this.tableId) {
      this.loadStatistics(this.tableId);
    }
  }

  loadStatistics(id: string): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.statsService.getTableStatistics(id, 30).subscribe({
      next: (res) => {
        // Backend returns in DESC order, reverse for chronological chart view
        const points = [...(res.data_points || [])].reverse();
        this.dataPoints.set(points);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در بارگذاری تاریخچه آمار جدول';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  readonly chartOptions = computed<echarts.EChartsCoreOption>(() => {
    const points = this.dataPoints();
    const xLabels = points.map((p) => {
      try {
        return this.persianDateFormatter.format(new Date(p.recorded_at));
      } catch {
        return p.recorded_at;
      }
    });

    const rowCounts = points.map((p) => p.row_count_estimate);
    const sizesMB = points.map((p) => Number((p.size_bytes / (1024 * 1024)).toFixed(2)));

    return {
      textStyle: {
        fontFamily: 'Vazirmatn, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      },
      tooltip: {
        trigger: 'axis',
        textStyle: {
          fontFamily: 'Vazirmatn, sans-serif',
        },
      },
      legend: {
        data: ['تعداد رکوردها (Rows)', 'حجم داده (MB)'],
        top: 0,
        textStyle: {
          fontFamily: 'Vazirmatn, sans-serif',
        },
      },
      grid: {
        left: '4%',
        right: '4%',
        bottom: '8%',
        top: '18%',
        containLabel: true,
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: xLabels,
        axisLabel: {
          fontFamily: 'Vazirmatn, sans-serif',
          rotate: xLabels.length > 8 ? 30 : 0,
        },
      },
      yAxis: [
        {
          type: 'value',
          name: 'تعداد رکوردها (Rows)',
          position: 'right',
          axisLabel: {
            fontFamily: 'Vazirmatn, sans-serif',
          },
        },
        {
          type: 'value',
          name: 'حجم (مگابایت)',
          position: 'left',
          axisLabel: {
            fontFamily: 'Vazirmatn, sans-serif',
          },
        },
      ],
      series: [
        {
          name: 'تعداد رکوردها (Rows)',
          type: 'line',
          yAxisIndex: 0,
          data: rowCounts,
          smooth: true,
          itemStyle: { color: '#2563eb' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(37, 99, 235, 0.25)' },
              { offset: 1, color: 'rgba(37, 99, 235, 0.02)' },
            ]),
          },
        },
        {
          name: 'حجم داده (MB)',
          type: 'line',
          yAxisIndex: 1,
          data: sizesMB,
          smooth: true,
          itemStyle: { color: '#16a34a' },
        },
      ],
    };
  });
}
