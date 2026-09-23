import { PropsWithChildren } from "react";
import { Navigate } from "react-router-dom";
import useAuthority from "@/utils/hooks/useAuthority";
import { useAppSelector } from "@/store";

type AuthorityGuardProps = PropsWithChildren<{
  userAuthority?: string[];
  authority?: string[];
}>;

const AuthorityGuard = (props: AuthorityGuardProps) => {
  const { userAuthority = [], authority = [], children } = props;

  const roleMatched = useAuthority(userAuthority, authority);
  const { permissions } = useAppSelector((state) => state.auth.user);

  const routeKey = children?.props?.children?.props?.routeKey;
  let permission = permissions?.[routeKey];
  if (routeKey == "accessDenied") permission = true;

  return <>{permission ? children : <Navigate to="/access-denied" />}</>;
};

export default AuthorityGuard;
