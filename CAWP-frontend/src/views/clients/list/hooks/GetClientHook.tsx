import { useCallback } from "react";
import {
  useAppDispatch,
  useAppSelector,
  getClients,
} from "@/views/clients/list/store";

export const useFetchClients = () => {
  const dispatch = useAppDispatch();
  const { page, pageSize, sort, query } = useAppSelector(
    (state) => state.clientsList.data.tableData
  );
  const filterData = useAppSelector(
    (state) => state.clientsList.data.filterData
  );

  const fetchData = useCallback(() => {
    dispatch(getClients({ page, pageSize, sort, query, filterData }));
  }, [page, pageSize, sort, query, filterData, dispatch]);

  return fetchData;
};
