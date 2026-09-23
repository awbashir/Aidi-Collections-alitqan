import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { FormItem, FormContainer } from "@/components/ui/Form";
import Alert from "@/components/ui/Alert";
import PasswordInput from "@/components/shared/PasswordInput";
import ActionLink from "@/components/shared/ActionLink";
import useTimeOutMessage from "@/utils/hooks/useTimeOutMessage";
import useAuth from "@/utils/hooks/useAuth";
import { Field, Form, Formik } from "formik";
import * as Yup from "yup";
import type { CommonProps } from "@/@types/common";
import { useTranslation } from "react-i18next";

interface SignInFormProps extends CommonProps {
  disableSubmit?: boolean;
  forgotPasswordUrl?: string;
  signUpUrl?: string;
}

type SignInFormSchema = {
  username: string;
  password: string;
  rememberMe: boolean;
};

const SignInForm = (props: SignInFormProps) => {
  const { t } = useTranslation();

  const validationSchema = Yup.object().shape({
    username: Yup.string().required(t("auth.username_required")),
    password: Yup.string().required(t("auth.password_required")),
    rememberMe: Yup.bool(),
  });
  const {
    disableSubmit = false,
    className,
    forgotPasswordUrl = "/forgot-password",
    signUpUrl = "/sign-up",
  } = props;

  const [message, setMessage] = useTimeOutMessage();

  const { signIn } = useAuth();

  const onSignIn = async (
    values: SignInFormSchema,
    setSubmitting: (isSubmitting: boolean) => void
  ) => {
    const { username, password } = values;
    setSubmitting(true);

    const result = await signIn({ username, password });

    if (result?.status === "failed") {
      setMessage(result.message);
    }

    setSubmitting(false);
  };

  return (
    <div className={className}>
      {message && (
        <Alert showIcon className="mb-4" type="danger">
          <>{message}</>
        </Alert>
      )}
      <Formik
        initialValues={{
          username: "",
          password: "",
          rememberMe: true,
        }}
        validationSchema={validationSchema}
        onSubmit={(values, { setSubmitting }) => {
          if (!disableSubmit) {
            onSignIn(values, setSubmitting);
          } else {
            setSubmitting(false);
          }
        }}
      >
        {({ touched, errors, isSubmitting }) => (
          <Form>
            <FormContainer>
              <FormItem
                label={t("auth.user_name")}
                invalid={(errors.username && touched.username) as boolean}
                errorMessage={errors.username}
              >
                <Field
                  type="text"
                  autoComplete="off"
                  name="username"
                  placeholder={t("auth.user_name")}
                  component={Input}
                />
              </FormItem>
              <FormItem
                label={t("auth.password")}
                invalid={(errors.password && touched.password) as boolean}
                errorMessage={errors.password}
              >
                <Field
                  autoComplete="off"
                  name="password"
                  placeholder={t("auth.password")}
                  component={PasswordInput}
                />
              </FormItem>
              <div className="flex justify-between mb-6">
                <Field className="mb-0" name="rememberMe" component={Checkbox}>
                  {t("auth.remember_me")}
                </Field>
                {/*  <ActionLink to={forgotPasswordUrl}>Forgot Password?</ActionLink> */}
              </div>
              <Button
                block
                loading={isSubmitting}
                variant="solid"
                type="submit"
              >
                {isSubmitting ? t("auth.Signing_in") : t("auth.sign_in")}
              </Button>
              {/*  <div className="mt-4 text-center">
                <span>{`Don't have an account yet?`} </span>
                <ActionLink to={signUpUrl}>Sign up</ActionLink>
              </div> */}
            </FormContainer>
          </Form>
        )}
      </Formik>
    </div>
  );
};

export default SignInForm;
