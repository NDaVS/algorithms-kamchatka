import type { ReactNode } from "react";

const I = ({ children, size = 20 }: { children: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
export const IconPlay = () => <I><path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" /></I>;
export const IconPause = () => <I><rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor" stroke="none" /><rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor" stroke="none" /></I>;
export const IconPlus = () => <I><path d="M12 5v14M5 12h14" /></I>;
export const IconMinus = () => <I><path d="M5 12h14" /></I>;
export const IconFit = () => <I><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></I>;
export const IconSearch = () => <I size={18}><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></I>;
export const IconWarn = () => <I size={16}><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17.2v.1" /></I>;
export const IconClose = () => <I><path d="M6 6l12 12M18 6L6 18" /></I>;
export const IconCheck = () => <I><path d="M5 12.5l4.5 4.5L19 7.5" /></I>;
export const IconPlow = () => (
  <I size={24}><path d="M3 7l5 3v7" /><path d="M8 17h9a3 3 0 003-3v-3h-5l-2-4H9" /><circle cx="10" cy="19" r="1.6" /><circle cx="17" cy="19" r="1.6" /></I>
);
export const IconDeadhead = () => <I size={24}><path d="M4 12h4M10 12h4M16 12h4" /><path d="M17 8l4 4-4 4" /></I>;
export const IconAlert = () => <I size={20}><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.1" /></I>;
export const IconPhone = () => <I size={18}><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></I>;
export const IconBack = () => <I><path d="M15 5l-7 7 7 7" /></I>;
export const IconLocate = () => <I><circle cx="12" cy="12" r="3.5" /><path d="M12 2.5v4M12 17.5v4M2.5 12h4M17.5 12h4" /></I>;
export const IconChevron = () => <I size={16}><path d="M6 9l6 6 6-6" /></I>;
