/* eslint-disable no-undef */
/** @type {import('tailwindcss').Config} */
const aidi = require("./src/assets/aidi/tailwind.aidi.cjs");

const safeListFile = "safelist.txt";

/**
 * Aidi AI Enterprise re-skin.
 *
 * The Elstar template builds colour classes at runtime from
 * `${themeColor}-${primaryColorLevel}` (see useThemeClass + ~27 UI primitives),
 * so every permutation it can produce has to exist in safelist.txt.
 * We pin themeColor to `navy` and only safelist the handful of scales the
 * runtime can actually reach — instead of the whole Tailwind palette.
 */
const SAFELIST_COLORS = [
  "navy",
  "gray",
  "azure",
  "signal",
  "success",
  "warning",
  "danger",
  "info",
  /* `red` and `blue` stay in because call sites pass them to Button as a
   * runtime `color="red-600"` / `color="blue-600"` prop (32 sites). */
  "red",
  "blue",
];

const SAFELIST_PREFIXES = [
  "text",
  "bg",
  "ring",
  "border",
  "hover:text",
  "hover:bg",
  "hover:ring",
  "active:bg",
  "focus:ring",
  "focus:border",
  "focus-within:ring",
  "focus-within:border",
  "dark:text",
  "dark:bg",
  "dark:border",
  "dark:hover:text",
  "dark:hover:bg",
  "dark:active:bg",
];

/* The only `h-${n}` / `w-${n}` strings the runtime builds come from
 * CONTROL_SIZES (Button, Input, Segment, InputGroup/Addon, Form/FormItem). */
const SAFELIST_METRICS = [
  "h-7",
  "h-8",
  "h-9",
  "h-10",
  "w-7",
  "w-8",
  "w-9",
  "w-10",
];

/* Classes assembled from props rather than written out in source. */
const SAFELIST_LITERALS = ["text-night-raised", "dark:text-night-raised"];

/* Built by src/utils/hooks/useTwColorByName.ts (notification avatars). */
const SAFELIST_AVATARS = [
  "navy-600",
  "ink-700",
  "azure-600",
  "azure-500",
  "lavender-500",
  "gray-600",
  "gray-700",
  "viz-3",
].flatMap((c) => [`bg-${c}`, `dark:bg-${c}`]);

const safelistPatterns = [
  ...SAFELIST_PREFIXES.flatMap((prefix) =>
    SAFELIST_COLORS.map((color) => `${prefix}-{colors.${color}}`)
  ),
  ...SAFELIST_METRICS,
  ...SAFELIST_AVATARS,
  ...SAFELIST_LITERALS,
];

/* Navy is the template's `themeColor`. The scale is shaped so that the
 * template's `${themeColor}-${primaryColorLevel}` machinery lands on the spec:
 *   600 rest · 700 hover · 800/900 active · 500 azure (level-100 = focus/link). */
const navy = {
  50: "#EEF3FD",
  100: "#D9E4FA",
  200: "#B3C8F4",
  300: "#7D9DEA",
  400: "#5482E5",
  500: "#2F5FD6",
  600: "#070A4B",
  700: "#14184F",
  800: "#04062F",
  900: "#04062F",
};

/* Aidi neutrals, mapped onto Tailwind's `gray` so every `@apply text-gray-*`
 * in the 34 component stylesheets re-tunes at once. */
const gray = {
  50: "#FAFCFD",
  100: "#F2F6F8",
  200: "#E7EEF2",
  300: "#D7E1E8",
  400: "#B8C4CE",
  500: "#8A929F",
  600: "#6B7482",
  700: "#2E3450",
  800: "#0C1033",
  900: "#060820",
  950: "#030413",
};

/* ---------------------------------------------------------------------------
 * Phase B — semantic hue re-mapping.
 *
 * `src/views/**` hard-codes stock Tailwind hues (blue 89, emerald 88, green 56,
 * orange 17, amber 14, purple/sky/teal/pink 8, yellow 6, rose 2). Rather than
 * sweep ~300 call sites, each stock hue is re-tuned onto the Aidi ramp that
 * carries the same meaning, so every existing class lands on the spec.
 *
 *   emerald, green          -> success  (#128A5C)
 *   blue, sky               -> azure    (#2F5FD6)
 *   orange, amber, yellow   -> warning  (#C86A00)   [never brand orange]
 *   rose, pink              -> danger   (= red, #B3261E)
 *   teal, cyan              -> steel    (#5B93C9)
 *   purple, violet          -> lavender (#8E86B8)
 *   neutral                 -> the Aidi neutral ramp (= gray)
 * ------------------------------------------------------------------------- */

/* Success */
const success = {
  50: "#E4F2EC",
  100: "#CDE8DA",
  200: "#A8D8C0",
  300: "#79C4A1",
  400: "#3EA87B",
  500: "#128A5C",
  600: "#0F7A52",
  700: "#0B5F40",
  800: "#084730",
  900: "#05301F",
  950: "#021D12",
};

/* Azure — interactivity */
const azureRamp = {
  50: "#EEF3FD",
  100: "#D9E4FA",
  200: "#B3C8F4",
  300: "#7D9DEA",
  400: "#5482E5",
  500: "#2F5FD6",
  600: "#234BB0",
  700: "#1A3A8A",
  800: "#142C68",
  900: "#0E1F4A",
  950: "#070A4B",
};

/* Warning — the only home for anything that used to be orange/amber/yellow.
 * Brand orange `#FF6600` lives in `signal` and is never reachable from a hue. */
const warning = {
  50: "#FBEBDD",
  100: "#F7DCC2",
  200: "#F0C494",
  300: "#E5A863",
  400: "#D88A2E",
  500: "#C86A00",
  600: "#B25A00",
  700: "#8F4800",
  800: "#6E3800",
  900: "#4C2700",
  950: "#2E1700",
};

/* Steel — categorical hue 3 */
const steel = {
  50: "#EFF5FA",
  100: "#DCE9F3",
  200: "#BCD5E8",
  300: "#93BBDA",
  400: "#75A6CF",
  500: "#5B93C9",
  600: "#4176A8",
  700: "#325C84",
  800: "#244260",
  900: "#17293C",
  950: "#0E1A27",
};

/* Lavender — categorical hues 5 / 6 */
const lavenderRamp = {
  50: "#F3F2F8",
  100: "#E7E5F0",
  200: "#CFCBE1",
  300: "#B2AAC4",
  400: "#9E95C0",
  500: "#8E86B8",
  600: "#736A9C",
  700: "#5A527B",
  800: "#413B59",
  900: "#2B2739",
  950: "#1B1826",
};

/* Destructive / invalid states in the template are hard-coded to `red-500`. */
const red = {
  50: "#FDF6F5",
  100: "#F9E6E4",
  200: "#F2CCC9",
  300: "#E9AFAA",
  400: "#D4655C",
  500: "#B3261E",
  600: "#B3261E",
  700: "#8F1E17",
  800: "#6E1712",
  900: "#4C100D",
};

module.exports = {
  mode: "jit",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}", "./safelist.txt"],
  darkMode: "class",
  theme: {
    fontFamily: {
      /* Latin glyphs and digits render in Anodina; Arabic glyphs fall through
       * to Manifa V2 (size-adjust: 130%). That keeps numerals Western-Arabic
       * and tabular in both directions, per the RTL spec. */
      sans: [
        "Anodina",
        '"Manifa V2"',
        '"Noto Sans Arabic"',
        '"Segoe UI"',
        "system-ui",
        "-apple-system",
        "sans-serif",
      ],
      arabic: ['"Manifa V2"', '"Noto Sans Arabic"', "sans-serif"],
      serif: [
        "ui-serif",
        "Georgia",
        "Cambria",
        '"Times New Roman"',
        "Times",
        "serif",
      ],
      mono: [
        "ui-monospace",
        "SFMono-Regular",
        "Menlo",
        "Monaco",
        "Consolas",
        '"Liberation Mono"',
        '"Courier New"',
        "monospace",
      ],
    },
    screens: {
      xs: "576",
      sm: "640px",
      md: "768px",
      lg: "1024px",
      xl: "1280px",
      "2xl": "1536px",
    },
    extend: {
      colors: {
        ...aidi.colors,
        signal: {
          ...aidi.colors.signal,
          /* rest / hover / active for the one Signal button per screen */
          50: "#FFF4EC",
          700: "#CC5200",
        },
        navy,
        indigo: navy,
        gray,
        neutral: gray,
        red,
        /* Phase B semantic re-mapping of the stock hues used across views. */
        emerald: success,
        green: success,
        blue: azureRamp,
        sky: azureRamp,
        orange: warning,
        amber: warning,
        yellow: warning,
        rose: red,
        pink: red,
        teal: steel,
        cyan: steel,
        purple: lavenderRamp,
        violet: lavenderRamp,
      },
      fontSize: aidi.fontSize,
      borderRadius: {
        none: "0",
        xs: "4px",
        sm: "4px",
        DEFAULT: "6px",
        md: "6px",
        lg: "8px",
        xl: "12px",
        "2xl": "12px",
        "3xl": "16px",
        pill: "999px",
        full: "9999px",
      },
      boxShadow: {
        none: "none",
        sm: "0 1px 2px rgba(7,10,75,.06)",
        DEFAULT: "0 1px 2px rgba(7,10,75,.06)",
        md: "0 8px 24px rgba(7,10,75,.10)",
        lg: "0 8px 24px rgba(7,10,75,.10)",
        xl: "0 24px 60px rgba(7,10,75,.16)",
        "2xl": "0 24px 60px rgba(7,10,75,.16)",
        raised: "0 1px 2px rgba(7,10,75,.06)",
        overlay: "0 8px 24px rgba(7,10,75,.10)",
        modal: "0 24px 60px rgba(7,10,75,.16)",
        focus: "0 0 0 2px #fff, 0 0 0 4px #2F5FD6",
        field: "0 0 0 2px rgba(47,95,214,.22)",
      },
      height: aidi.height,
      spacing: aidi.spacing,
      transitionTimingFunction: aidi.transitionTimingFunction,
      transitionDuration: aidi.transitionDuration,
      typography: (theme) => ({
        DEFAULT: {
          css: {
            color: theme("colors.gray.700"),
            maxWidth: "65ch",
          },
        },
        invert: {
          css: {
            color: theme("colors.night.body"),
          },
        },
      }),
    },
  },
  plugins: [
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("./twSafelistGenerator")({
      path: safeListFile,
      patterns: safelistPatterns,
    }),
    require("@tailwindcss/typography"),
  ],
};
