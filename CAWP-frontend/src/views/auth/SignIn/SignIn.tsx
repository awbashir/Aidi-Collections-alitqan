import SignInForm from "./SignInForm";
import { useTranslation } from "react-i18next";

const SignIn = () => {
  const { t } = useTranslation();

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
      <SignInForm disableSubmit={false} />
    </>
  );
};

export default SignIn;
