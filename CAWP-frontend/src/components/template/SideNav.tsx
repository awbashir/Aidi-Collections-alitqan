import classNames from "classnames";
import ScrollBar from "@/components/ui/ScrollBar";
import {
  SIDE_NAV_WIDTH,
  SIDE_NAV_COLLAPSED_WIDTH,
  NAV_MODE_DARK,
  SIDE_NAV_CONTENT_GUTTER,
  LOGO_X_GUTTER,
} from "@/constants/theme.constant";
import Logo from "@/components/template/Logo";
import navigationConfig from "@/configs/navigation.config";
import VerticalMenuContent from "@/components/template/VerticalMenuContent";
import useResponsive from "@/utils/hooks/useResponsive";
import { useAppSelector } from "@/store";

const sideNavStyle = {
  width: SIDE_NAV_WIDTH,
  minWidth: SIDE_NAV_WIDTH,
};

const sideNavCollapseStyle = {
  width: SIDE_NAV_COLLAPSED_WIDTH,
  minWidth: SIDE_NAV_COLLAPSED_WIDTH,
};

const SideNav = () => {
  const navMode = useAppSelector((state) => state.theme.navMode);
  const mode = useAppSelector((state) => state.theme.mode);
  const direction = useAppSelector((state) => state.theme.direction);
  const currentRouteKey = useAppSelector(
    (state) => state.base.common.currentRouteKey
  );
  const sideNavCollapse = useAppSelector(
    (state) => state.theme.layout.sideNavCollapse
  );
  const userAuthority = useAppSelector((state) => state.auth.user.authority);

  const { larger } = useResponsive();

  /* The nav is white in light and #0C1033 in dark — it never takes the theme
     colour. `themed` / `transparent` collapse onto the same white chrome. */
  const sideNavColor = () => `side-nav-${navMode}`;

  const logoMode = () => (mode === NAV_MODE_DARK ? NAV_MODE_DARK : "light");

  const menuContent = (
    <VerticalMenuContent
      navMode={navMode}
      collapsed={sideNavCollapse}
      navigationTree={navigationConfig}
      routeKey={currentRouteKey}
      userAuthority={userAuthority as string[]}
      direction={direction}
    />
  );

  return (
    <>
      {larger.md && (
        <div
          style={sideNavCollapse ? sideNavCollapseStyle : sideNavStyle}
          className={classNames(
            "side-nav",
            sideNavColor(),
            !sideNavCollapse && "side-nav-expand"
          )}
        >
          <div className="side-nav-header">
            <Logo
              mode={logoMode()}
              type={sideNavCollapse ? "streamline" : "full"}
              className={
                sideNavCollapse ? SIDE_NAV_CONTENT_GUTTER : LOGO_X_GUTTER
              }
              logoWidth="100%"
            />
          </div>
          {sideNavCollapse ? (
            menuContent
          ) : (
            <div className="side-nav-content">
              <ScrollBar autoHide direction={direction}>
                {menuContent}
              </ScrollBar>
            </div>
          )}
        </div>
      )}
    </>
  );
};

export default SideNav;
