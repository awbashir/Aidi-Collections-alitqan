import { cloneElement } from "react";
import LanguageSelector from "@/components/template/LanguageSelector";
import { TENANT_NAME } from "@/constants/app.constant";
import { useAppSelector } from "@/store";
import type { ReactNode, ReactElement } from "react";
import type { CommonProps } from "@/@types/common";

interface SimpleProps extends CommonProps {
  content?: ReactNode;
}

/**
 * Aidi Enterprise sign-in shell: a navy brand panel beside a #FAFCFD form
 * panel holding a white, 8px-radius, bordered card. Everything is laid out
 * with logical properties, so `dir="rtl"` mirrors it without a second variant.
 */
const Simple = ({ children, content, ...rest }: SimpleProps) => {
  const mode = useAppSelector((state) => state.theme.mode);
  const isDark = mode === "dark";

  return (
    <div className="grid h-full lg:grid-cols-2 bg-gray-50 dark:bg-night-page">
      <div className="flex flex-col items-center justify-center px-6 py-12">
        <div className="w-full max-w-[400px] flex flex-col gap-6">
          <div className="flex flex-col gap-4">
            {/* The lockup is fixed artwork — it does not mirror under RTL. */}
            <img
              dir="ltr"
              className="h-6 w-auto self-start block"
              src={`/aidi/aidi-wordmark-${isDark ? "white" : "navy"}.svg`}
              alt="Aidi"
            />
            <div className="text-[11px] font-extrabold text-gray-500 dark:text-night-muted">
              {TENANT_NAME}
            </div>
          </div>

          <div className="rounded-lg border border-gray-300 dark:border-night-border bg-white dark:bg-night-card p-6">
            {content}
            {children
              ? cloneElement(children as ReactElement, {
                  ...rest,
                })
              : null}
          </div>

          <div className="flex items-center justify-between gap-4">
            <p className="text-[12px] leading-relaxed text-gray-500 dark:text-night-muted">
              الدخول إلى المنصة مقيَّد بصلاحيات حسابك، وكل إجراء مسجَّل في سجل
              التدقيق.
            </p>
            <div className="shrink-0">
              <LanguageSelector />
            </div>
          </div>
        </div>
      </div>

      <div
        className="relative hidden lg:flex overflow-hidden"
        style={{
          background:
            "linear-gradient(210deg,#070A4B 0%,#0B0F55 46%,#14184F 100%)",
        }}
      >
        <div
          aria-hidden="true"
          className="absolute w-[560px] h-[560px] rounded-full"
          style={{
            insetInlineStart: "-140px",
            bottom: "-200px",
            background:
              "radial-gradient(circle at 60% 40%,rgba(255,102,0,.30) 0%,rgba(255,102,0,0) 62%)",
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage: "url('/aidi/aidi-mark-white.svg')",
            backgroundSize: "72px",
            backgroundRepeat: "repeat",
            maskImage: "linear-gradient(to top,#000,transparent 72%)",
            WebkitMaskImage: "linear-gradient(to top,#000,transparent 72%)",
          }}
        />
        <div className="relative flex flex-col justify-end gap-5 p-14 w-full">
          <h2 className="!text-[40px] !leading-[1.28] !text-white max-w-[20ch] [text-wrap:balance]">
            التحصيل بذكاء. والقرار بوضوح.
          </h2>
          <p className="text-[17px] leading-[1.7] text-night-body max-w-[44ch]">
            منصة فنية الاتقان لإدارة محافظ التحصيل — من إسناد الحسابات إلى
            المتابعة والتقارير، في مكان واحد.
          </p>
          <div className="flex gap-9 flex-wrap pt-4 mt-2 border-t border-white/[.14]">
            <div className="flex flex-col gap-1.5">
              <div className="text-[12px] font-extrabold text-azure-300">
                مُحوكَم
              </div>
              <div className="text-[13.5px] leading-[1.7] text-night-body">
                صلاحيات المستخدمين تنتقل كما هي
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="text-[12px] font-extrabold text-azure-300">
                مُدقَّق
              </div>
              <div className="text-[13.5px] leading-[1.7] text-night-body">
                كل إجراء مسجَّل مع وقته
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Simple;
