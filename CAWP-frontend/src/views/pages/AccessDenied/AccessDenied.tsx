import Container from "@/components/shared/Container";
import DoubleSidedImage from "@/components/shared/DoubleSidedImage";
import { useTranslation } from "react-i18next";

const AccessDenied = () => {
  const { t } = useTranslation();
  return (
    <Container className="h-full">
      <div className="h-full flex flex-col items-center justify-center">
        <DoubleSidedImage
          src="/img/others/img-2.png"
          darkModeSrc="/img/others/img-2-dark.png"
          alt="Access Denied"
        />
        <div className="mt-6 text-center">
          <h3 className="mb-2 text-[20px] font-extrabold text-navy-600 dark:text-night-body">
            {t("access_denied")}
          </h3>
          <p className="text-[15px] text-gray-600 dark:text-night-muted">
            {t("access_denied_message")}
          </p>
        </div>
      </div>
    </Container>
  );
};

export default AccessDenied;
