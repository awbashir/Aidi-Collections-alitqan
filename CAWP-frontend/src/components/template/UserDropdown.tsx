import Avatar from "@/components/ui/Avatar";
import Dropdown from "@/components/ui/Dropdown";
import withHeaderItem from "@/utils/hoc/withHeaderItem";
import useAuth from "@/utils/hooks/useAuth";
import { Link } from "react-router-dom";
import classNames from "classnames";
import { HiOutlineLogout, HiOutlineUser } from "react-icons/hi";
import type { CommonProps } from "@/@types/common";
import { useAppSelector } from "@/store";
import { useTranslation } from "react-i18next";

type DropdownList = {
  label: string;
  path: string;
  icon: JSX.Element;
};

const dropdownItemList: DropdownList[] = [];

const _UserDropdown = ({ className }: CommonProps) => {
  const { t } = useTranslation();
  const { avatar, username, authority, email, name, permission_name } =
    useAppSelector((state) => state.auth.user);
  const { signOut } = useAuth();

  const initials = (name || username || "")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part: string) => part[0])
    .join("");

  const UserAvatar = (
    <div className={classNames(className, "flex items-center gap-2.5")}>
      <Avatar
        size={32}
        shape="circle"
        className="bg-navy-600 dark:bg-azure-600 text-white text-[12.5px] font-extrabold"
      >
        {initials || <HiOutlineUser />}
      </Avatar>
      <div className="hidden md:block ltr:text-left rtl:text-right leading-tight">
        <div className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">
          {name}
        </div>
        <div className="text-[11px] font-extrabold text-gray-500 dark:text-night-muted">
          {permission_name}
        </div>
      </div>
    </div>
  );

  return (
    <div>
      <Dropdown
        menuStyle={{ minWidth: 240 }}
        renderTitle={UserAvatar}
        placement="bottom-end"
      >
        <Dropdown.Item variant="header">
          <div className="py-2 px-3 flex items-center gap-2">
            <Avatar
              shape="circle"
              className="bg-navy-600 dark:bg-azure-600 text-white text-[13px] font-extrabold"
            >
              {initials || <HiOutlineUser />}
            </Avatar>
            <div>
              <div className="font-extrabold text-navy-600 dark:text-gray-100">
                {username}
              </div>
              <div className="text-[12px] text-gray-600 dark:text-night-muted" dir="ltr">
                {email}
              </div>
            </div>
          </div>
        </Dropdown.Item>
        <Dropdown.Item variant="divider" />
        {dropdownItemList.map((item) => (
          <Dropdown.Item
            key={item.label}
            eventKey={item.label}
            className="mb-1 px-0"
          >
            <Link className="flex h-full w-full px-2" to={item.path}>
              <span className="flex gap-2 items-center w-full">
                <span className="text-xl opacity-50">{item.icon}</span>
                <span>{item.label}</span>
              </span>
            </Link>
          </Dropdown.Item>
        ))}
        {/* <Dropdown.Item variant="divider" /> */}
        <Dropdown.Item eventKey="Sign Out" className="gap-2" onClick={signOut}>
          <span className="text-xl opacity-50">
            <HiOutlineLogout />
          </span>
          <span>{t("auth.sign_out")}</span>
        </Dropdown.Item>
      </Dropdown>
    </div>
  );
};

const UserDropdown = withHeaderItem(_UserDropdown);

export default UserDropdown;
