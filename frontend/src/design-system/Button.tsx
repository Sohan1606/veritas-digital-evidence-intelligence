import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router";
import { Icon, type IconName } from "./icons";

type Variant = "primary" | "secondary" | "ghost" | "quiet";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium transition-micro select-none " +
  "disabled:cursor-not-allowed disabled:opacity-45 aria-disabled:cursor-not-allowed aria-disabled:opacity-55";

const variants: Record<Variant, string> = {
  primary:
    "bg-fg text-ink-950 hover:bg-white shadow-[0_0_0_1px_rgba(255,255,255,0.08),0_8px_24px_-12px_rgba(94,196,208,0.45)]",
  secondary: "border border-line-strong bg-ink-750 text-fg hover:border-fg-faint hover:bg-ink-700",
  ghost: "text-fg-muted hover:bg-ink-750 hover:text-fg",
  quiet: "border border-line text-fg-muted hover:border-line-strong hover:text-fg",
};

const sizes: Record<Size, string> = {
  sm: "h-7 rounded-sm px-2.5 text-xs",
  md: "h-8 rounded-sm px-3 text-[0.8125rem]",
  lg: "h-11 rounded-md px-5 text-sm",
};

export function buttonClass(variant: Variant = "secondary", size: Size = "md", extra = "") {
  return `${base} ${variants[variant]} ${sizes[size]} ${extra}`;
}

interface CommonProps {
  variant?: Variant;
  size?: Size;
  icon?: IconName;
  trailingIcon?: IconName;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, CommonProps & ButtonHTMLAttributes<HTMLButtonElement>>(
  function Button({ variant, size, icon, trailingIcon, className = "", children, type = "button", ...rest }, ref) {
    return (
      <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...rest}>
        {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
        {children}
        {trailingIcon && <Icon name={trailingIcon} size={size === "sm" ? 14 : 16} />}
      </button>
    );
  },
);

export function ButtonLink({ variant, size, icon, trailingIcon, className = "", children, ...rest }: CommonProps & LinkProps) {
  return (
    <Link className={buttonClass(variant, size, className)} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
      {children}
      {trailingIcon && <Icon name={trailingIcon} size={size === "sm" ? 14 : 16} />}
    </Link>
  );
}
