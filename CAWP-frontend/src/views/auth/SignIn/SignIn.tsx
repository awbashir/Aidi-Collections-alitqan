import { useEffect, useState } from "react";
import SignInForm from "./SignInForm";
import { useTranslation } from "react-i18next";
import Alert from "@/components/ui/Alert";
import {
  clearSignOutReason,
  peekSignOutReason,
} from "@/utils/v2/sessionEnd";

const SignIn = () => {
  const { t, i18n } = useTranslation();
  /* Phase 3: why the previous session ended (recorded by the 401 interceptor).
   * Shown in both languages: the user may not have chosen one yet. */
  const [reason] = useState(peekSignOutReason);
  useEffect(() => {
    clearSignOutReason();
  }, []);
  const other = (i18n.language || "").startsWith("ar") ? "en" : "ar";
  const tOther = i18n.getFixedT(other);

  return (
    <>
      <div className="mb-5">
        <h1 className="!text-[22px] !leading-[1.35] mb-1">
          {t("auth.sign_in")}
        </h1>
        <p className="text-[13px] text-gray-600 dark:text-night-muted">
          {t("auth.login_prompt")}
        </p>
      </div>
      {reason && (
        <Alert
          showIcon
          type={reason === "revoked" ? "warning" : "info"}
          className="mb-4"
          data-testid="p3-session-ended"
          data-reason={reason}
        >
          <div className="font-extrabold">{t(`p3.session.${reason}Title`)}</div>
          <div>{t(`p3.session.${reason}Body`)}</div>
          <div
            dir={other === "ar" ? "rtl" : "ltr"}
            lang={other}
            className="mt-1 text-[12.5px] opacity-80"
          >
            {tOther(`p3.session.${reason}Title`)} {tOther(`p3.session.${reason}Body`)}
          </div>
        </Alert>
      )}
      <SignInForm disableSubmit={false} />
    </>
  );
};

export default SignIn;
