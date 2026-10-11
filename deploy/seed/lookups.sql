-- Generic lookup lists for a clean installation (no customer, contract, status or user data).
-- Exported from the reference database on 2026-10-11. IDs are kept because the frontend
-- hardcodes some of them (ContactMethods mobile/phone/email, comment_reachability 'reached').
-- Load once into an empty database after manage_db.py up: psql -v ON_ERROR_STOP=1 -f lookups.sql

INSERT INTO public."ClintContactTypes" (id, name, create_at, update_at, is_deleted) VALUES ('cddd230b-ce12-4019-b6d7-f151aad6bcdb', 'mobile', '2024-07-06 11:08:51.314515+00', '2024-07-06 11:08:51.314515+00', false);
INSERT INTO public."ClintContactTypes" (id, name, create_at, update_at, is_deleted) VALUES ('76af77e1-a3ee-4e79-95b4-d9d8d9680466', 'email', '2024-07-06 11:08:51.318584+00', '2024-07-06 11:08:51.318584+00', false);
INSERT INTO public."ClintContactTypes" (id, name, create_at, update_at, is_deleted) VALUES ('46660a11-6df6-497a-9f82-091224a90265', 'phone', '2024-07-06 11:08:51.319711+00', '2024-07-06 11:08:51.319711+00', false);
INSERT INTO public."ClintContactTypes" (id, name, create_at, update_at, is_deleted) VALUES ('4ebd108a-9429-41fa-80a9-a50e35c3ffac', 'fax', '2024-07-06 11:08:51.320912+00', '2024-07-06 11:08:51.320912+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('d5bfb3d3-4e99-4e2c-98e6-9a709461af15', 'كاش', '2024-07-06 10:08:43.117452+00', '2024-07-06 10:08:43.117452+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('7367dab2-d479-4cfe-a48a-e2ede1def3aa', 'شيك', '2024-07-06 10:08:57.845868+00', '2024-07-06 10:08:57.845868+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('77eadf16-1b56-43fa-8564-1c850dff8a6d', 'تحويل', '2024-07-06 10:09:08.11464+00', '2024-07-06 10:09:08.11464+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('720a207d-6d65-41ee-8ff7-8bae6a3355e9', 'ايداع بالحساب', '2024-07-06 10:09:30.995145+00', '2024-07-06 10:09:30.995145+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('ceeddc54-6504-4012-bb30-a3a7864c588e', 'إعفاء', '2025-08-21 09:31:31.017709+00', '2025-08-21 09:31:31.017709+00', false);
INSERT INTO public."CollectionMethods" (id, name, create_at, update_at, is_deleted) VALUES ('f6200f06-d64f-4d5f-8146-e34f3ecfe3bd', 'سداد محكمة', '2024-09-03 08:20:55.132203+00', '2024-09-03 08:20:55.132203+00', false);
INSERT INTO public."CollectionTypes" (id, name, create_at, update_at, is_deleted) VALUES ('f1c9c44d-0e0f-4929-9079-767acad7946d', 'نقدي', '2024-07-06 13:16:25.438618+00', '2024-07-06 13:16:25.438618+00', false);
INSERT INTO public."CollectionTypes" (id, name, create_at, update_at, is_deleted) VALUES ('b163d90a-66bc-4f2e-a2b3-8f59ad6e3afc', 'مجدول', '2024-07-06 13:16:25.441509+00', '2024-07-06 13:16:25.441509+00', false);
INSERT INTO public."ContactMethods" (id, name) VALUES ('b55aa031-9349-4a3a-ad14-1f9d6821a342', 'هاتف');
INSERT INTO public."ContactMethods" (id, name) VALUES ('ff081c7e-4500-4a75-ae36-580f2ae90834', 'جوال');
INSERT INTO public."ContactMethods" (id, name) VALUES ('25a02e31-9b4a-4940-9ef4-283f38d4b52d', 'بريد الاكتروني');
INSERT INTO public."ContactMethods" (id, name) VALUES ('99606e56-3ebe-4e75-927a-c8f8361e71a1', 'رسالة نصية');
INSERT INTO public."ContactMethods" (id, name) VALUES ('cf71968a-5c1a-4ef2-9e83-ad69d6cfb65b', 'واتساب');
INSERT INTO public."ContactMethods" (id, name) VALUES ('2566834b-025f-4e2e-9118-1445ef449860', 'تشيك');
INSERT INTO public."archiveReason" (id, name) VALUES ('f2184113-d08d-4438-a1fe-a4db32789464', 'منتهي');
INSERT INTO public."archiveReason" (id, name) VALUES ('21eb71df-4aee-4ab8-ae52-9eefaa1d1fda', 'مسحوب');
INSERT INTO public."archiveReason" (id, name) VALUES ('b8e94b6f-2a38-4fd1-9845-ac75425369b6', 'مكرر برقم حساب مختلف	');
INSERT INTO public.comment_reachability (id, name, is_deleted, create_at) VALUES ('9d45dcb5-25fa-4b9b-aa03-3488e85ad81a', 'تم التوصل', false, '2024-07-06 12:25:49.050223+00');
INSERT INTO public.comment_reachability (id, name, is_deleted, create_at) VALUES ('81a4180b-876b-443e-a6fd-62a693887370', 'لم يتم التوصل', false, '2024-07-06 12:25:49.056698+00');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('04356546-cb6f-4f91-833e-a52b0d0948d7', 'راتب خارج البنك');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('4b1d4bc7-0194-47a1-9e07-5459970225d1', 'راتب على البنك');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('b426d8a7-e3df-4c62-af8c-10fe91339eb4', 'متقاعد راتب بنك آخر');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('8ad62170-feca-46cb-9473-8e053352811f', 'مفصول');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('da54bc72-6bdf-4164-94c3-417e45c3aa7e', 'متقاعد مبكر');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('952b585a-0f3b-4a3d-8e7c-ab0d3ef87782', 'متقاعد نظامي');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('83664c3a-9f88-447d-97b2-8580353a4dc2', 'متوفي');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('cab38217-7efc-463b-aff5-108f2830aa4e', 'متقاعد اجباري');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('fa1c49b9-fac3-404f-a529-856b56214245', 'مسجون');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('f27ca82a-a614-414b-b1ec-1d6e5b3df929', 'مبتعث');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('d3c3048c-c2f3-4285-9a24-8b6cdc67ad45', 'خروج نهائي');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('3331a977-769f-440d-82a6-68522de3f992', 'تآخير الرواتب');
INSERT INTO public."delinquencyReason" (id, name) VALUES ('573cb5b9-9605-42e7-8e5e-7d4e03c5bef1', 'إلتزامات عالية');
INSERT INTO public.updatesides (id, name, created_at, updated_at, is_active) VALUES ('152f3477-642f-4a21-ac20-5af32b0387e6', 'مصادر معلومات	', '2024-07-06 10:10:54.78668+00', '2024-07-06 10:10:54.78668+00', true);
INSERT INTO public.updatesides (id, name, created_at, updated_at, is_active) VALUES ('4b8d2a33-079c-4573-a203-85a2140a4c0b', 'مصادر معلومات	٢', '2024-07-06 10:11:03.540723+00', '2024-07-06 10:11:03.540723+00', true);
INSERT INTO public.updatesides (id, name, created_at, updated_at, is_active) VALUES ('3d5ebed8-2050-4bbb-864e-26ff30fac2e8', 'مصادر معلومات	٣', '2024-07-06 10:11:08.912146+00', '2024-07-06 10:11:08.912146+00', true);
