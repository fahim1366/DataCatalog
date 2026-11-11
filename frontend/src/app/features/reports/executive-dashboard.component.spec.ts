import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { of, throwError } from 'rxjs';
import { ExecutiveDashboardComponent } from './executive-dashboard.component';
import { CompletenessGaugeComponent } from './completeness-gauge.component';
import { ClassificationSunburstComponent } from './classification-sunburst.component';
import { ReportService } from './services/report.service';
import {
  CompletenessReport,
  ClassificationReport,
  ReportRefreshResponse,
} from './models/report.models';

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
              set: (target: any, prop: string | symbol, value: any) => {
                target[prop] = value;
                return true;
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

describe('Executive Governance Reporting Dashboard (T044: Apache ECharts Executive Widgets)', () => {
  const mockCompletenessReport: CompletenessReport = {
    overall_completeness_pct: 84.5,
    total_databases: 800,
    total_tables: 160000,
    total_columns: 5000000,
    database_breakdown: [
      {
        connection_id: '11111111-1111-1111-1111-111111111111',
        database_name: 'prod_orders_db',
        table_count: 320,
        tables_with_desc: 290,
        tables_with_owner: 310,
        completeness_pct: 90.63,
      },
      {
        connection_id: '22222222-2222-2222-2222-222222222222',
        database_name: 'analytics_dw',
        table_count: 850,
        tables_with_desc: 680,
        tables_with_owner: 720,
        completeness_pct: 80.0,
      },
    ],
  };

  const mockClassificationReport: ClassificationReport = {
    total_classified_columns: 42150,
    breakdown: [
      {
        classification_level: 'PUBLIC',
        column_count: 25000,
        affected_table_count: 8200,
      },
      {
        classification_level: 'INTERNAL',
        column_count: 12000,
        affected_table_count: 4500,
      },
      {
        classification_level: 'CONFIDENTIAL',
        column_count: 3500,
        affected_table_count: 1200,
      },
      {
        classification_level: 'RESTRICTED',
        column_count: 1650,
        affected_table_count: 550,
      },
    ],
  };

  const mockRefreshResponse: ReportRefreshResponse = {
    status: 'SUCCESS',
    message: 'Materialized views refreshed concurrently.',
    refreshed_views: ['mv_metadata_completeness', 'mv_classification_distribution'],
  };

  describe('CompletenessGaugeComponent', () => {
    let fixture: ComponentFixture<CompletenessGaugeComponent>;
    let component: CompletenessGaugeComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [CompletenessGaugeComponent],
      }).compileComponents();

      fixture = TestBed.createComponent(CompletenessGaugeComponent);
      component = fixture.componentInstance;
    });

    it('should create the CompletenessGaugeComponent instance', () => {
      expect(component).toBeTruthy();
    });

    it('should compute gauge chart options with Canvas renderer and score data', () => {
      fixture.componentRef.setInput('score', 84.5);
      fixture.detectChanges();

      const options = component.chartOptions();
      expect(options).toBeDefined();
      expect(options['series']).toBeDefined();
      const series = (options['series'] as any)[0];
      expect(series.type).toBe('gauge');
      expect(series.min).toBe(0);
      expect(series.max).toBe(100);
      expect(series.data[0].value).toBe(84.5);
      expect(series.data[0].name).toBe('Completeness');
    });

    it('should update chart options dynamically when score input changes', () => {
      fixture.componentRef.setInput('score', 92.0);
      fixture.detectChanges();

      const updatedOptions = component.chartOptions();
      const series = (updatedOptions['series'] as any)[0];
      expect(series.data[0].value).toBe(92.0);
    });
  });

  describe('ClassificationSunburstComponent', () => {
    let fixture: ComponentFixture<ClassificationSunburstComponent>;
    let component: ClassificationSunburstComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [ClassificationSunburstComponent],
      }).compileComponents();

      fixture = TestBed.createComponent(ClassificationSunburstComponent);
      component = fixture.componentInstance;
    });

    it('should create the ClassificationSunburstComponent instance', () => {
      expect(component).toBeTruthy();
    });

    it('should compute sunburst chart options mapping classification breakdown data', () => {
      fixture.componentRef.setInput('report', mockClassificationReport);
      fixture.detectChanges();

      const options = component.chartOptions();
      expect(options).toBeDefined();
      expect(options['series']).toBeDefined();
      const series = (options['series'] as any)[0];
      expect(series.type).toBe('sunburst');
      expect(series.data.length).toBe(4);
      expect(series.data[0].name).toBe('PUBLIC');
      expect(series.data[0].value).toBe(25000);
      expect(series.data[3].name).toBe('RESTRICTED');
      expect(series.data[3].value).toBe(1650);
    });
  });

  describe('ExecutiveDashboardComponent', () => {
    let fixture: ComponentFixture<ExecutiveDashboardComponent>;
    let component: ExecutiveDashboardComponent;
    let reportService: ReportService;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [
          ExecutiveDashboardComponent,
          CompletenessGaugeComponent,
          ClassificationSunburstComponent,
        ],
        providers: [
          ReportService,
          provideHttpClient(),
          provideHttpClientTesting(),
        ],
      }).compileComponents();

      reportService = TestBed.inject(ReportService);
      vi.spyOn(reportService, 'getCompletenessReport').mockReturnValue(of(mockCompletenessReport));
      vi.spyOn(reportService, 'getClassificationDistribution').mockReturnValue(
        of(mockClassificationReport)
      );
      vi.spyOn(reportService, 'refreshMaterializedViews').mockReturnValue(of(mockRefreshResponse));

      fixture = TestBed.createComponent(ExecutiveDashboardComponent);
      component = fixture.componentInstance;
    });

    it('should create the ExecutiveDashboardComponent instance', () => {
      expect(component).toBeTruthy();
    });

    it('should fetch completeness and classification reports on init', async () => {
      fixture.detectChanges();
      await fixture.whenStable();

      expect(reportService.getCompletenessReport).toHaveBeenCalledTimes(1);
      expect(reportService.getClassificationDistribution).toHaveBeenCalledTimes(1);

      expect(component.completenessReport()).toEqual(mockCompletenessReport);
      expect(component.classificationReport()).toEqual(mockClassificationReport);
      expect(component.isLoading()).toBe(false);
    });

    it('should render executive KPI metric cards for databases, tables, columns, and classified columns', async () => {
      fixture.detectChanges();
      await fixture.whenStable();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('800'); // total databases
      expect(compiled.textContent).toContain('160,000'); // total tables
      expect(compiled.textContent).toContain('5,000,000'); // total columns
      expect(compiled.textContent).toContain('42,150'); // total classified columns
      expect(compiled.textContent).toContain('84.5%'); // overall completeness
    });

    it('should render CompletenessGaugeComponent and ClassificationSunburstComponent widgets', async () => {
      fixture.detectChanges();
      await fixture.whenStable();

      const gaugeDebug = fixture.debugElement.query(By.directive(CompletenessGaugeComponent));
      expect(gaugeDebug).toBeTruthy();
      expect(gaugeDebug.componentInstance.score()).toBe(84.5);

      const sunburstDebug = fixture.debugElement.query(
        By.directive(ClassificationSunburstComponent)
      );
      expect(sunburstDebug).toBeTruthy();
      expect(sunburstDebug.componentInstance.report()).toEqual(mockClassificationReport);
    });

    it('should render database breakdown table rows with completeness percentages', async () => {
      fixture.detectChanges();
      await fixture.whenStable();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('prod_orders_db');
      expect(compiled.textContent).toContain('90.63%');
      expect(compiled.textContent).toContain('analytics_dw');
      expect(compiled.textContent).toContain('80%');
    });

    it('should trigger materialized view concurrent refresh when refresh button is clicked', async () => {
      fixture.detectChanges();
      await fixture.whenStable();

      const refreshBtn = fixture.debugElement.query(By.css('[data-testid="refresh-reports-btn"]'));
      expect(refreshBtn).toBeTruthy();

      refreshBtn.nativeElement.click();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(reportService.refreshMaterializedViews).toHaveBeenCalledTimes(1);
      expect(reportService.getCompletenessReport).toHaveBeenCalledTimes(2);
      expect(reportService.getClassificationDistribution).toHaveBeenCalledTimes(2);
      expect(component.refreshSuccessMessage()).toContain('Materialized views refreshed');
    });

    it('should handle service errors gracefully and display error alert', async () => {
      vi.spyOn(reportService, 'getCompletenessReport').mockReturnValue(
        throwError(() => new Error('Materialized view query failed'))
      );

      component.loadReports();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.errorMessage()).toContain('Materialized view query failed');
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Materialized view query failed');
    });
  });
});
