import { useCallback } from "react";
import {
  useAppDispatch,
  useAppSelector,
  getClients,
} from "@/views/clients2/list/store";

export const useFetchClients = () => {
  const dispatch = useAppDispatch();
  const { page, pageSize, sort, query } = useAppSelector(
    (state) => state.clientsList2.data.tableData
  );
  const filterData = useAppSelector(
    (state) => state.clientsList2.data.filterData
  );

  const fetchData = useCallback(() => {
    dispatch(getClients({ page, pageSize, sort, query, filterData }));
  }, [page, pageSize, sort, query, filterData, dispatch]);

  return fetchData;
};
