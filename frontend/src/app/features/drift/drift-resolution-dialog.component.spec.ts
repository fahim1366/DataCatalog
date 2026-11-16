import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { By } from '@angular/platform-browser';
import { DriftResolutionDialogComponent } from './drift-resolution-dialog.component';
import { DriftBannerComponent } from './drift-banner.component';
import {
  DescriptionDriftDetailResponse,
  DescriptionDriftResolutionResponse,
  DriftService,
} from '../../core/services/drift.service';

const mockDriftDetail: DescriptionDriftDetailResponse = {
  table_id: 'table-uuid-123',
  table_name: 'dim_customers',
  table_source_description: 'Source DB comment: Raw customer dimension from ERP',
  table_approved_description: 'Approved: جدول بعد مشتریان تایید شده برای گزارشات فروش',
  drift_detected: true,
  column_drifts: [
    {
      column_id: 'col-uuid-1',
      column_name: 'national_code',
      source_description: 'National identity code 10 digits',
      approved_description: 'کد ملی ۱۰ رقمی با فرمت اعتبارسنجی',
      drift_detected: true,
    },
    {
      column_id: 'col-uuid-2',
      column_name: 'phone_number',
      source_description: 'Customer mobile number',
      approved_description: 'شماره تلفن همراه مشتری',
      drift_detected: false,
    },
  ],
};

describe('DriftBannerComponent & DriftResolutionDialogComponent (T083)', () => {
  describe('DriftBannerComponent', () => {
    let fixture: ComponentFixture<DriftBannerComponent>;
    let component: DriftBannerComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [DriftBannerComponent],
      }).compileComponents();

      fixture = TestBed.createComponent(DriftBannerComponent);
      component = fixture.componentInstance;
    });

    it('should NOT render warning banner when drift_detected is false', () => {
      component.driftDetected = false;
      fixture.detectChanges();

      const banner = fixture.debugElement.query(By.css('.drift-warning-banner'));
      expect(banner).toBeNull();
    });

    it('should render warning banner when drift_detected is true and emit on resolve click', () => {
      component.driftDetected = true;
      component.tableName = 'dim_customers';
      fixture.detectChanges();

      const banner = fixture.debugElement.query(By.css('.drift-warning-banner'));
      expect(banner).toBeTruthy();
      expect(banner.nativeElement.textContent).toContain('مغایرت در توضیحات');

      const resolveBtn = fixture.debugElement.query(By.css('.btn-open-drift-resolution'));
      expect(resolveBtn).toBeTruthy();

      const emitSpy = vi.spyOn(component.openResolution, 'emit');
      resolveBtn.nativeElement.click();
      expect(emitSpy).toHaveBeenCalled();
    });
  });

  describe('DriftResolutionDialogComponent', () => {
    let fixture: ComponentFixture<DriftResolutionDialogComponent>;
    let component: DriftResolutionDialogComponent;
    let driftService: DriftService;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [DriftResolutionDialogComponent],
        providers: [
          DriftService,
          provideHttpClient(),
          provideHttpClientTesting(),
        ],
      }).compileComponents();

      driftService = TestBed.inject(DriftService);
      vi.spyOn(driftService, 'getTableDrift').mockReturnValue(of(mockDriftDetail));

      fixture = TestBed.createComponent(DriftResolutionDialogComponent);
      component = fixture.componentInstance;
    });

    it('should render side-by-side comparison of approved vs source descriptions', () => {
      component.tableId = 'table-uuid-123';
      component.visible = true;
      component.ngOnChanges({
        visible: {
          currentValue: true,
          previousValue: false,
          firstChange: true,
          isFirstChange: () => true,
        },
      });

      fixture.detectChanges();

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('Source DB comment: Raw customer dimension from ERP');
      expect(text).toContain('Approved: جدول بعد مشتریان تایید شده برای گزارشات فروش');
      expect(text).toContain('national_code');
    });

    it('should display 3 resolution radio actions: Retain Approved, Overwrite with Source, and Custom Merge', () => {
      component.tableId = 'table-uuid-123';
      component.visible = true;
      component.driftData.set(mockDriftDetail);
      fixture.detectChanges();

      const radioOptions = fixture.debugElement.queryAll(By.css('.drift-radio-option'));
      expect(radioOptions.length).toBe(3);

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('حفظ توضیحات تأییدشده');
      expect(text).toContain('اعمال توضیحات پایگاه‌داده مبدأ');
      expect(text).toContain('مقایسه و ویرایش دستی');
    });

    it('should submit resolution to DriftService on confirm click', () => {
      component.tableId = 'table-uuid-123';
      component.visible = true;
      component.driftData.set(mockDriftDetail);
      component.selectedAction.set('OVERWRITE_WITH_SOURCE');
      fixture.detectChanges();

      const mockResponse: DescriptionDriftResolutionResponse = {
        success: true,
        message: 'Resolved',
        resolved_at: '2026-11-16T12:00:00Z',
      };
      const resolveSpy = vi.spyOn(driftService, 'resolveDrift').mockReturnValue(of(mockResponse));
      const resolvedEmitterSpy = vi.spyOn(component.resolved, 'emit');

      const submitBtn = fixture.debugElement.query(By.css('.btn-submit-resolution'));
      expect(submitBtn).toBeTruthy();
      submitBtn.nativeElement.click();

      expect(resolveSpy).toHaveBeenCalledWith('table-uuid-123', {
        resolution_action: 'OVERWRITE_WITH_SOURCE',
        custom_description: undefined,
        column_resolutions: [],
      });
      expect(resolvedEmitterSpy).toHaveBeenCalled();
    });
  });
});
