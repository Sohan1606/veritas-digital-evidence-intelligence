import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
  /** Hide on narrow screens when the value is secondary. */
  secondary?: boolean;
}

/**
 * Dense, accessible data table. Below the `md` breakpoint rows reflow into stacked
 * cards (same DOM, CSS only) with each cell labelled by its column header.
 */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  isSelected,
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  isSelected?: (row: T) => boolean;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[0.8125rem] max-md:block">
        <caption className="sr-only">{caption}</caption>
        <thead className="max-md:sr-only">
          <tr className="border-b border-line">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`whitespace-nowrap px-4 py-2.5 font-mono text-2xs font-normal uppercase tracking-[0.12em] text-fg-subtle ${column.className ?? ""}`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="max-md:block max-md:space-y-2 max-md:p-2">
          {rows.map((row) => {
            const selected = isSelected?.(row) ?? false;
            return (
              <tr
                key={rowKey(row)}
                aria-selected={isSelected ? selected : undefined}
                className={`border-b border-line/70 transition-micro last:border-b-0 hover:bg-ink-750/60 max-md:block max-md:rounded-md max-md:border max-md:p-3 ${
                  selected ? "bg-ink-750 shadow-[inset_2px_0_0_var(--color-signal)]" : ""
                }`}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    data-label={column.header}
                    className={`px-4 py-2.5 align-top max-md:flex max-md:gap-3 max-md:px-0 max-md:py-1 max-md:before:w-28 max-md:before:shrink-0 max-md:before:font-mono max-md:before:text-2xs max-md:before:uppercase max-md:before:tracking-[0.1em] max-md:before:text-fg-subtle max-md:before:content-[attr(data-label)] ${
                      column.secondary ? "max-sm:hidden" : ""
                    } ${column.className ?? ""}`}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
