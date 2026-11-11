import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { of } from 'rxjs';
import { VersionDiffComponent } from './version-diff.component';
import { VersionService } from './services/version.service';
import { TableVersionItem, VersionDiffResponse } from './models/version.models';

const mockDiffResponse: VersionDiffResponse = {
  table_id: '33333333-3333-3333-3333-333333333333',
  v1_num: 1,
  v2_num: 2,
  added_columns: [
    {
      name: 'total_amount',
      data_type: 'NUMERIC(12,2)',
      raw_data_type: 'numeric(12,2)',
      ordinal_position: 4,
      is_nullable: false,
      is_primary_key: false,
      is_foreign_key: false,
    },
    {
      name: 'discount_pct',
      data_type: 'DECIMAL(5,2)',
      raw_data_type: 'decimal(5,2)',
      ordinal_position: 5,
      is_nullable: true,
      is_primary_key: false,
      is_foreign_key: false,
    },
  ],
  removed_columns: [
    {
      name: 'notes',
      data_type: 'VARCHAR(200)',
      raw_data_type: 'varchar(200)',
      ordinal_position: 2,
      is_nullable: true,
      is_primary_key: false,
      is_foreign_key: false,
    },
  ],
  modified_columns: [
    {
      column_name: 'status',
      v1_attributes: {
        data_type: 'VARCHAR(20)',
        raw_data_type: 'varchar(20)',
        is_nullable: false,
      },
      v2_attributes: {
        data_type: 'TEXT',
        raw_data_type: 'text',
        is_nullable: true,
      },
    },
  ],
};

const mockVersionsList: TableVersionItem[] = [
  {
    id: 'v-001',
    table_id: '33333333-3333-3333-3333-333333333333',
    version_num: 1,
    created_by_username: 'admin',
    created_at: '2026-11-10T10:00:00Z',
  },
  {
    id: 'v-002',
    table_id: '33333333-3333-3333-3333-333333333333',
    version_num: 2,
    created_by_username: 'data_engineer',
    created_at: '2026-11-10T12:00:00Z',
  },
];

describe('VersionDiffComponent (T034: Angular Schema Diff Viewer Component)', () => {
  let fixture: ComponentFixture<VersionDiffComponent>;
  let component: VersionDiffComponent;
  let versionService: VersionService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [VersionDiffComponent],
      providers: [
        VersionService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    versionService = TestBed.inject(VersionService);
    vi.spyOn(versionService, 'compareVersions').mockReturnValue(of(mockDiffResponse));
    vi.spyOn(versionService, 'getVersions').mockReturnValue(of(mockVersionsList));

    fixture = TestBed.createComponent(VersionDiffComponent);
    component = fixture.componentInstance;
  });

  it('should create the VersionDiffComponent instance', () => {
    expect(component).toBeTruthy();
  });

  describe('Direct diffData Input Mode', () => {
    beforeEach(async () => {
      fixture.componentRef.setInput('diffData', mockDiffResponse);
      fixture.detectChanges();
      await fixture.whenStable();
    });

    it('should render side-by-side header displaying compared version numbers', () => {
      const header = fixture.debugElement.query(By.css('.diff-header'));
      expect(header).toBeTruthy();
      const text = header.nativeElement.textContent;
      expect(text).toContain('Version 1');
      expect(text).toContain('Version 2');
    });

    it('should render summary counter badges with correct color-coded classes', () => {
      const addedBadge = fixture.debugElement.query(By.css('.summary-badge-added'));
      const removedBadge = fixture.debugElement.query(By.css('.summary-badge-removed'));
      const modifiedBadge = fixture.debugElement.query(By.css('.summary-badge-modified'));

      expect(addedBadge).toBeTruthy();
      expect(addedBadge.nativeElement.textContent).toContain('2 Added');
      expect(addedBadge.nativeElement.classList).toContain('badge-green');

      expect(removedBadge).toBeTruthy();
      expect(removedBadge.nativeElement.textContent).toContain('1 Removed');
      expect(removedBadge.nativeElement.classList).toContain('badge-red');

      expect(modifiedBadge).toBeTruthy();
      expect(modifiedBadge.nativeElement.textContent).toContain('1 Modified');
      expect(modifiedBadge.nativeElement.classList).toContain('badge-amber');
    });

    it('should render added columns side-by-side with green diff badges', () => {
      const addedRows = fixture.debugElement.queryAll(By.css('.diff-row-added'));
      expect(addedRows.length).toBe(2);

      const firstAddedRow = addedRows[0];
      // Left side (v1) should indicate absence or placeholder
      const v1Cell = firstAddedRow.query(By.css('.diff-cell-v1'));
      expect(v1Cell.nativeElement.textContent.trim()).toBe('—');
      expect(v1Cell.nativeElement.classList).toContain('cell-empty');

      // Right side (v2) should show column name, data type and green badge
      const v2Cell = firstAddedRow.query(By.css('.diff-cell-v2'));
      expect(v2Cell.nativeElement.textContent).toContain('total_amount');
      expect(v2Cell.nativeElement.textContent).toContain('NUMERIC(12,2)');

      const greenBadge = firstAddedRow.query(By.css('.badge-added, .badge-green'));
      expect(greenBadge).toBeTruthy();
      expect(greenBadge.nativeElement.textContent).toContain('ADDED');
    });

    it('should render removed columns side-by-side with red diff badges', () => {
      const removedRows = fixture.debugElement.queryAll(By.css('.diff-row-removed'));
      expect(removedRows.length).toBe(1);

      const removedRow = removedRows[0];
      // Left side (v1) should display column name, data type and red badge
      const v1Cell = removedRow.query(By.css('.diff-cell-v1'));
      expect(v1Cell.nativeElement.textContent).toContain('notes');
      expect(v1Cell.nativeElement.textContent).toContain('VARCHAR(200)');

      const redBadge = removedRow.query(By.css('.badge-removed, .badge-red'));
      expect(redBadge).toBeTruthy();
      expect(redBadge.nativeElement.textContent).toContain('REMOVED');

      // Right side (v2) should indicate absence or placeholder
      const v2Cell = removedRow.query(By.css('.diff-cell-v2'));
      expect(v2Cell.nativeElement.textContent.trim()).toBe('—');
      expect(v2Cell.nativeElement.classList).toContain('cell-empty');
    });

    it('should render modified column types side-by-side with amber diff badges', () => {
      const modifiedRows = fixture.debugElement.queryAll(By.css('.diff-row-modified'));
      expect(modifiedRows.length).toBe(1);

      const modifiedRow = modifiedRows[0];
      // Left side (v1) should show original type
      const v1Cell = modifiedRow.query(By.css('.diff-cell-v1'));
      expect(v1Cell.nativeElement.textContent).toContain('status');
      expect(v1Cell.nativeElement.textContent).toContain('VARCHAR(20)');

      // Right side (v2) should show updated type
      const v2Cell = modifiedRow.query(By.css('.diff-cell-v2'));
      expect(v2Cell.nativeElement.textContent).toContain('status');
      expect(v2Cell.nativeElement.textContent).toContain('TEXT');

      // Amber badge indicating type change
      const amberBadge = modifiedRow.query(By.css('.badge-modified, .badge-amber'));
      expect(amberBadge).toBeTruthy();
      expect(amberBadge.nativeElement.textContent).toContain('MODIFIED');

      // Detailed diff chip showing attribute change
      const diffChip = modifiedRow.query(By.css('.attribute-diff-chip'));
      expect(diffChip).toBeTruthy();
      expect(diffChip.nativeElement.textContent).toContain('VARCHAR(20)');
      expect(diffChip.nativeElement.textContent).toContain('TEXT');
    });

    it('should allow toggling between side-by-side view and categorized tabs', async () => {
      const categoryTabBtn = fixture.debugElement.query(By.css('.btn-view-categorized'));
      expect(categoryTabBtn).toBeTruthy();

      categoryTabBtn.nativeElement.click();
      fixture.detectChanges();
      await fixture.whenStable();

      const sections = fixture.debugElement.queryAll(By.css('.diff-category-section'));
      expect(sections.length).toBe(3); // Added, Removed, Modified sections
    });
  });

  describe('Empty Diff State', () => {
    it('should display an empty state banner when no changes exist between versions', async () => {
      const emptyDiff: VersionDiffResponse = {
        table_id: '33333333-3333-3333-3333-333333333333',
        v1_num: 1,
        v2_num: 1,
        added_columns: [],
        removed_columns: [],
        modified_columns: [],
      };

      fixture.componentRef.setInput('diffData', emptyDiff);
      fixture.detectChanges();
      await fixture.whenStable();

      const emptyBanner = fixture.debugElement.query(By.css('.diff-empty-state'));
      expect(emptyBanner).toBeTruthy();
      expect(emptyBanner.nativeElement.textContent).toContain('No schema changes detected');
    });
  });

  describe('Service Fetch Mode (tableId + v1 + v2)', () => {
    it('should fetch diff from VersionService when tableId, v1, and v2 inputs are set', async () => {
      fixture.componentRef.setInput('tableId', '33333333-3333-3333-3333-333333333333');
      fixture.componentRef.setInput('v1', 1);
      fixture.componentRef.setInput('v2', 2);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(versionService.compareVersions).toHaveBeenCalledWith(
        '33333333-3333-3333-3333-333333333333',
        1,
        2
      );

      const header = fixture.debugElement.query(By.css('.diff-header'));
      expect(header).toBeTruthy();
      expect(header.nativeElement.textContent).toContain('Version 1');
      expect(header.nativeElement.textContent).toContain('Version 2');
    });
  });
});

