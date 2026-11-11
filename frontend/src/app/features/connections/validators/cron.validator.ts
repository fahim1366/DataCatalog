import { AbstractControl, ValidationErrors, ValidatorFn } from '@angular/forms';

const MONTH_NAMES: Record<string, number> = {
  JAN: 1,
  FEB: 2,
  MAR: 3,
  APR: 4,
  MAY: 5,
  JUN: 6,
  JUL: 7,
  AUG: 8,
  SEP: 9,
  OCT: 10,
  NOV: 11,
  DEC: 12,
};

const DOW_NAMES: Record<string, number> = {
  SUN: 0,
  MON: 1,
  TUE: 2,
  WED: 3,
  THU: 4,
  FRI: 5,
  SAT: 6,
};

interface FieldBound {
  name: string;
  min: number;
  max: number;
  nameMap?: Record<string, number>;
}

const FIELD_BOUNDS: FieldBound[] = [
  { name: 'minute', min: 0, max: 59 },
  { name: 'hour', min: 0, max: 23 },
  { name: 'day of month', min: 1, max: 31 },
  { name: 'month', min: 1, max: 12, nameMap: MONTH_NAMES },
  { name: 'day of week', min: 0, max: 7, nameMap: DOW_NAMES },
];

function parseToken(token: string, nameMap?: Record<string, number>): number {
  const upper = token.toUpperCase();
  if (nameMap && upper in nameMap) {
    return nameMap[upper];
  }
  if (!/^\d+$/.test(token)) {
    throw new Error(`Invalid token: '${token}'`);
  }
  return parseInt(token, 10);
}

function validateField(
  fieldStr: string,
  bound: FieldBound
): void {
  if (!fieldStr || fieldStr.trim() === '') {
    throw new Error(`Field '${bound.name}' cannot be empty`);
  }

  const items = fieldStr.split(',');
  for (const item of items) {
    if (!item) {
      throw new Error(`Empty element in field '${bound.name}'`);
    }

    let base = item;
    if (item.includes('/')) {
      const parts = item.split('/');
      if (parts.length !== 2) {
        throw new Error(`Invalid step expression '${item}' in field '${bound.name}'`);
      }
      base = parts[0];
      const stepStr = parts[1];
      if (!/^\d+$/.test(stepStr)) {
        throw new Error(`Step '${stepStr}' must be a positive integer in field '${bound.name}'`);
      }
      const step = parseInt(stepStr, 10);
      if (step < 1) {
        throw new Error(`Step must be >= 1 in field '${bound.name}'`);
      }
      if (base === '*') {
        continue;
      }
    }

    if (base === '*') {
      continue;
    }

    if (base.includes('-')) {
      const rangeParts = base.split('-');
      if (rangeParts.length !== 2) {
        throw new Error(`Invalid range expression '${base}' in field '${bound.name}'`);
      }
      const start = parseToken(rangeParts[0], bound.nameMap);
      const end = parseToken(rangeParts[1], bound.nameMap);
      if (start < bound.min || start > bound.max || end < bound.min || end > bound.max) {
        throw new Error(`Range values '${base}' must be between ${bound.min} and ${bound.max} in field '${bound.name}'`);
      }
      if (start > end) {
        throw new Error(`Range start '${start}' must be <= end '${end}' in field '${bound.name}'`);
      }
    } else {
      const val = parseToken(base, bound.nameMap);
      if (val < bound.min || val > bound.max) {
        throw new Error(`Value '${val}' out of range [${bound.min}, ${bound.max}] in field '${bound.name}'`);
      }
    }
  }
}

export function cronValidator(): ValidatorFn {
  return (control: AbstractControl): ValidationErrors | null => {
    const value = control.value;
    if (value === null || value === undefined || value === '') {
      return null;
    }

    if (typeof value !== 'string') {
      return { invalidCron: { message: 'Cron expression must be a string' } };
    }

    const trimmed = value.trim();
    if (trimmed === '') {
      return null;
    }

    const fields = trimmed.split(/\s+/);
    if (fields.length !== 5) {
      return {
        invalidCron: {
          message: `Expected exactly 5 fields (<minute> <hour> <day of month> <month> <day of week>), got ${fields.length}`,
        },
      };
    }

    try {
      for (let i = 0; i < FIELD_BOUNDS.length; i++) {
        validateField(fields[i], FIELD_BOUNDS[i]);
      }
      return null;
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : 'Invalid cron syntax';
      return {
        invalidCron: {
          message: errorMsg,
        },
      };
    }
  };
}

