import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { TableStatisticsChartComponent } from './table-statistics-chart.component';
import { TableStatsService, TableStatisticsHistoryResponse } from '../../core/services/table-stats.service';

// Setup Mock Canvas 2D context and ResizeObserver for jsdom environment
beforeAll(() => {
  if (typeof globalThis !== 'undefined') {
    if (!(globalThis as any).ResizeObserver) {
      (globalThis as any).ResizeObserver = class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
  }

  if (typeof window !== 'undefined') {
    if (!window.ResizeObserver) {
      (window as any).ResizeObserver = class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }

    if (window.HTMLCanvasElement) {
      const originalGetContext = window.HTMLCanvasElement.prototype.getContext;
      window.HTMLCanvasElement.prototype.getContext = function (contextId: string, ...args: any[]): any {
        if (contextId === '2d') {
          const mockCtx: any = new Proxy(
            {
              canvas: this,
              measureText: () => ({ width: 0 }),
            },
            {
              get: (target: any, prop: string | symbol) => {
                if (prop in target) return target[prop];
                if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
                  return () => ({ addColorStop: () => {} });
                }
                return () => {};
              },
            }
          );
          return mockCtx;
        }
        return originalGetContext ? originalGetContext.apply(this, [contextId, ...args] as any) : null;
      };
    }
  }
});

const mockStats: TableStatisticsHistoryResponse = {
  table_id: 'table-uuid-777',
  data_points: [
    {
      recorded_at: '2026-11-14T08:00:00Z',
      row_count_estimate: 100000,
      size_bytes: 20971520, // 20 MB
    },
    {
      recorded_at: '2026-11-15T08:00:00Z',
      row_count_estimate: 115000,
      size_bytes: 24117248, // 23 MB
    },
    {
      recorded_at: '2026-11-16T08:00:00Z',
      row_count_estimate: 130000,
      size_bytes: 27262976, // 26 MB
    },
  ],
};

describe('TableStatisticsChartComponent (T084)', () => {
  let fixture: ComponentFixture<TableStatisticsChartComponent>;
  let component: TableStatisticsChartComponent;
  let statsService: TableStatsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TableStatisticsChartComponent],
      providers: [
        TableStatsService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    statsService = TestBed.inject(TableStatsService);
    vi.spyOn(statsService, 'getTableStatistics').mockReturnValue(of(mockStats));

    fixture = TestBed.createComponent(TableStatisticsChartComponent);
    component = fixture.componentInstance;
  });

  it('should load statistics data on tableId change and compute dual y-axis chart options', () => {
    component.tableId = 'table-uuid-777';
    component.ngOnChanges({
      tableId: {
        currentValue: 'table-uuid-777',
        previousValue: null,
        firstChange: true,
        isFirstChange: () => true,
      },
    });

    fixture.detectChanges();

    const options = component.chartOptions() as any;
    expect(options).toBeTruthy();

    // Check Dual Y-Axes
    expect(Array.isArray(options.yAxis)).toBe(true);
    expect(options.yAxis.length).toBe(2);
    expect(options.yAxis[0].name).toContain('تعداد');
    expect(options.yAxis[1].name).toContain('حجم');

    // Check Series (2 line series for row counts and volume)
    expect(options.series.length).toBe(2);
    expect(options.series[0].type).toBe('line');
    expect(options.series[0].yAxisIndex).toBe(0);
    expect(options.series[1].type).toBe('line');
    expect(options.series[1].yAxisIndex).toBe(1);

    // Check Shamsi Date X-Axis
    expect(options.xAxis.type).toBe('category');
    expect(options.xAxis.data.length).toBe(3);

    // Check Vazirmatn Font Styling
    expect(options.textStyle?.fontFamily).toContain('Vazirmatn');
  });
});
