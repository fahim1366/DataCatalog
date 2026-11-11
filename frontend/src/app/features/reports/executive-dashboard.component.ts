import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin } from 'rxjs';
import { ReportService } from './services/report.service';
import {
  ClassificationReport,
  CompletenessReport,
  DatabaseCompletenessItem,
} from './models/report.models';
import { CompletenessGaugeComponent } from './completeness-gauge.component';
import { ClassificationSunburstComponent } from './classification-sunburst.component';

@Component({
  selector: 'app-executive-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    CompletenessGaugeComponent,
    ClassificationSunburstComponent,
  ],
  templateUrl: './executive-dashboard.component.html',
  styleUrls: ['./executive-dashboard.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExecutiveDashboardComponent implements OnInit {
  private readonly reportService = inject(ReportService);

  readonly completenessReport = signal<CompletenessReport | null>(null);
  readonly classificationReport = signal<ClassificationReport | null>(null);
  readonly isLoading = signal<boolean>(false);
  readonly isRefreshing = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly refreshSuccessMessage = signal<string | null>(null);

  // Computed metrics for executive KPI cards
  readonly overallScore = computed<number>(
    () => this.completenessReport()?.overall_completeness_pct ?? 0
  );
  readonly totalDatabases = computed<number>(
    () => this.completenessReport()?.total_databases ?? 0
  );
  readonly totalTables = computed<number>(
    () => this.completenessReport()?.total_tables ?? 0
  );
  readonly totalColumns = computed<number>(
    () => this.completenessReport()?.total_columns ?? 0
  );
  readonly totalClassified = computed<number>(
    () => this.classificationReport()?.total_classified_columns ?? 0
  );
  readonly databaseBreakdown = computed<DatabaseCompletenessItem[]>(
    () => this.completenessReport()?.database_breakdown ?? []
  );

  ngOnInit(): void {
    this.loadReports();
  }

  loadReports(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    forkJoin({
      completeness: this.reportService.getCompletenessReport(),
      classification: this.reportService.getClassificationDistribution(),
    }).subscribe({
      next: ({ completeness, classification }) => {
        this.completenessReport.set(completeness);
        this.classificationReport.set(classification);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error ? err.message : 'Failed to load executive reports';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  refreshViews(): void {
    this.isRefreshing.set(true);
    this.refreshSuccessMessage.set(null);
    this.errorMessage.set(null);

    this.reportService.refreshMaterializedViews().subscribe({
      next: (response) => {
        this.refreshSuccessMessage.set(response.message);
        this.isRefreshing.set(false);
        this.loadReports();
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to refresh materialized views concurrently';
        this.errorMessage.set(msg);
        this.isRefreshing.set(false);
      },
    });
  }
}

