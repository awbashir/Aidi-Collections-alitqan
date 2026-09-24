import Header from "@/components/template/Header";
import SidePanel from "@/components/template/SidePanel";
import UserDropdown from "@/components/template/UserDropdown";
import HeaderLogo from "@/components/template/HeaderLogo";
import SecondaryHeader from "@/components/template/SecondaryHeader";
import MobileNav from "@/components/template/MobileNav";
import View from "@/views";
import LanguageSelector from "../template/LanguageSelector";
import SoftphoneShell from "@/components/softphone/SoftphoneShell";
import ModeSwitcher from "../template/ThemeConfigurator/ModeSwitcher";
import Notification from "@/components/template/Notification";

const HeaderActionsStart = () => {
  return (
    <>
      <HeaderLogo />
      <MobileNav />
    </>
  );
};

const HeaderActionsEnd = () => {
  return (
    <>
      <SoftphoneShell />
      <LanguageSelector />
      <ModeSwitcher />
      <SidePanel />
      <Notification />
      <UserDropdown hoverable={false} />
    </>
  );
};

const DeckedLayout = () => {
  return (
    <div className="app-layout-simple flex flex-auto flex-col min-h-screen">
      <div className="flex flex-auto min-w-0">
        <div className="flex flex-col flex-auto min-h-screen min-w-0 relative w-full">
          <Header
            container
            className="shadow dark:shadow-2xl"
            headerStart={<HeaderActionsStart />}
            headerEnd={<HeaderActionsEnd />}
          />
          <SecondaryHeader contained />
          <View pageContainerType="contained" />
        </div>
      </div>
    </div>
  );
};

export default DeckedLayout;
